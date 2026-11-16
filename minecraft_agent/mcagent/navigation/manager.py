"""Navigation task orchestration over Baritone (§7).

The manager resolves destinations (named locations from the registry, or
explicit coordinates), submits goals through the Baritone bridge, tracks and
monitors tasks, detects failure/timeout, supports cancellation and
interruption by other movement owners. It does NOT micromanage movement:
Baritone owns pathfinding; we only watch outcomes.
"""
from __future__ import annotations
import math
import time

from ..enums import NavState, TaskState, ErrorCode, MovementOwner
from ..config import current_config
from ..world.location_registry import LocationRegistry
from ..world.world_model import WorldModel
from ..world.database import valid_dimension
from ..agent.action_arbiter import ActionArbiter


class NavigationManager:
    def __init__(self, baritone_bridge, model: WorldModel, registry: LocationRegistry,
                 task_tracker, events, db, arbiter: ActionArbiter):
        self.bridge = baritone_bridge
        self.model = model
        self.registry = registry
        self.tasks = task_tracker
        self.events = events
        self.db = db
        self.arbiter = arbiter
        self._active: str | None = None
        self._paused: dict | None = None  # {destination, reason, paused_task}
        self._progress: dict = {}         # task_id -> (last_dist, no_progress_polls)
        self._started: dict = {}          # task_id -> timestamp

    def now(self) -> float:
        return time.monotonic()

    # ---------------- destination resolution ----------------

    def resolve(self, destination) -> dict:
        """Resolve named location or explicit coords; validates dimension."""
        if isinstance(destination, str):
            rec = self.registry.get(destination)
            if rec is None:
                return {"ok": False, "error": {
                    "code": ErrorCode.LOCATION_NOT_FOUND,
                    "message": f"no saved location named {destination}"}}
            return {"ok": True, "destination": destination,
                    "coords": {"x": rec.x, "y": rec.y, "z": rec.z},
                    "dimension": rec.dimension}
        if isinstance(destination, dict):
            try:
                coords = {"x": int(destination["x"]), "y": int(destination["y"]),
                          "z": int(destination["z"])}
            except (KeyError, TypeError, ValueError):
                return {"ok": False, "error": {
                    "code": ErrorCode.INVALID_ARGS,
                    "message": "destination needs integer x,y,z"}}
            dim = destination.get("dimension")
            if dim is None:
                dim = (self.model._player or {}).get("dimension", "minecraft:overworld")
            if not valid_dimension(dim):
                return {"ok": False, "error": {
                    "code": ErrorCode.INVALID_DIMENSION,
                    "message": f"unknown dimension {dim!r} in destination"}}
            cur = (self.model._player or {}).get("dimension")
            if cur and dim != cur:
                return {"ok": False, "error": {
                    "code": ErrorCode.INVALID_DIMENSION,
                    "message": f"destination is in {dim} but the player is in {cur}"}}
            return {"ok": True, "destination": f"({coords['x']},{coords['y']},{coords['z']})",
                    "coords": coords, "dimension": dim}
        return {"ok": False, "error": {
            "code": ErrorCode.INVALID_ARGS, "message": "destination must be a name or {x,y,z[,dimension]}"}}

    # ---------------- submit ----------------

    def navigate(self, destination) -> dict:
        """High-level navigate request; returns task_id + in_progress status."""
        res = self.resolve(destination)
        if not res["ok"]:
            return res
        coords = res["coords"]
        # duplicate-task guard: an identical active navigation is not resubmitted
        for t in self.tasks.active():
            if t["kind"] == "navigate" and t["meta"].get("coords") == coords:
                return {"ok": True, "duplicate": True, "task_id": t["id"],
                        "status": TaskState.IN_PROGRESS.value,
                        "destination": res["destination"]}
        if not self.arbiter.request(MovementOwner.NAVIGATION):
            return {"ok": False, "error": {
                "code": ErrorCode.MOVEMENT_BUSY,
                "message": "another controller owns movement right now"}}

        task_id = self.tasks.new("navigate", res["destination"],
                                 {"coords": coords, "dimension": res["dimension"]},
                                 now=self.now())
        submit = self.bridge.submit_navigate(task_id, coords, res["dimension"])
        if not submit["ok"]:
            self.tasks.transition(task_id, TaskState.FAILED, "submission refused", self.now())
            return {"ok": False, "error": {
                "code": ErrorCode.NAVIGATION_BRIDGE_UNAVAILABLE,
                "message": "navigation bridge refused the request"}}
        self._active = task_id
        self._started[task_id] = self.now()
        self._progress[task_id] = (None, 0)
        self.tasks.transition(task_id, TaskState.IN_PROGRESS, "goal submitted", self.now())
        dist = self.model.distance_to((coords["x"], coords["y"], coords["z"]))
        return {"ok": True, "task_id": task_id, "status": TaskState.IN_PROGRESS.value,
                "destination": res["destination"], "coords": coords,
                "distance": dist, "method": submit["method"]}

    # ---------------- monitoring ----------------

    def poll(self, task_id=None) -> dict:
        """Query backend status once; meaningful changes emit events (§8)."""
        task_id = task_id or self._active
        t = self.tasks.get(task_id) if task_id else None
        if t is None:
            return {"state": NavState.IDLE.value, "task": None}
        raw = self.bridge.poll(task_id)
        state = raw.get("state", "not_found")
        if state == "arrived":
            self.tasks.transition(task_id, TaskState.COMPLETED, "arrived", self.now())
            self.model.player_status(force=True)  # the bot actually moved
            self.events.emit("location_reached",
                             {"destination": t["destination"], "task_id": task_id},
                             now=self.now())
            self._active = None
            return {"state": NavState.COMPLETED.value, "task_id": task_id,
                    "task": t["kind"]}
        if state in ("blocked", "failed", "not_found"):
            self.tasks.transition(task_id, TaskState.FAILED, raw.get("detail", {}).get("reason", state),
                                  self.now())
            self.events.emit("navigation_failed",
                             {"task_id": task_id, "reason": state}, now=self.now())
            return {"state": NavState.FAILED.value, "task_id": task_id,
                    "reason": state, "detail": raw.get("detail")}
        if state == "cancelled":
            self.tasks.transition(task_id, TaskState.CANCELLED, "backend cancelled", self.now())
            self._active = None
            return {"state": NavState.CANCELLED.value, "task_id": task_id}

        # progress detection: prefer the backend's own remaining vector
        # (the authoritative view of the bot), fall back to our player view.
        p = self.model.player_position()
        dest = t["meta"]["coords"]
        rem = raw.get("detail", {}).get("remaining")
        if rem and len(rem) == 3:
            dist = round(math.sqrt(sum(v * v for v in rem)), 1)
        else:
            dist = round(math.sqrt(sum((p[i] - dest[k]) ** 2
                                      for i, k in enumerate("xyz"))), 1)
        last, stuck = self._progress.get(task_id, (None, 0))
        if last is not None and dist >= last - 0.5:
            stuck += 1
        else:
            stuck = 0
        self._progress[task_id] = (dist, stuck)
        cfg = current_config()
        started = self._started.get(task_id, self.now())
        if stuck >= cfg.stuck_polls:
            self.tasks.transition(task_id, TaskState.BLOCKED, "no progress", self.now())
            self.events.emit("navigation_failed",
                             {"task_id": task_id, "reason": "stuck"}, now=self.now())
            state_name = NavState.BLOCKED.value
        elif self.now() - started > cfg.nav_timeout_s:
            self.tasks.transition(task_id, TaskState.FAILED, "timeout", self.now())
            state_name = NavState.FAILED.value
        else:
            state_name = NavState.NAVIGATING.value
        return {"state": state_name, "task_id": task_id, "task": t["kind"],
                "destination": dest, "distance_remaining": dist,
                "progressing": stuck == 0}

    def get_navigation_status(self) -> dict:
        if not self._active:
            return {"state": NavState.IDLE.value, "task": None}
        out = self.poll(self._active)
        out["current_position"] = list(self.model.player_position())
        return out

    # ---------------- cancellation / interruption ----------------

    def cancel(self, task_id=None) -> dict:
        task_id = task_id or self._active
        t = self.tasks.get(task_id) if task_id else None
        if t is None:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "no active navigation task"}}
        res = self.bridge.cancel(task_id)
        self.tasks.transition(task_id, TaskState.CANCELLED, "user cancel", self.now())
        if task_id == self._active:
            self._active = None
        return {"ok": True, "cancelled": task_id, "raw": res}

    def interrupted_by(self, owner_reason: str, owner: str) -> dict:
        """Called when another controller (combat/emergency) takes ownership:
        cancel the in-flight task; remember it as resumable only if the
        destination remains valid (§7: never resume blindly)."""
        if self._active is None:
            return {"ok": True, "note": "nothing was navigating"}
        t = self.tasks.get(self._active)
        res = self.bridge.cancel(self._active)
        self.tasks.transition(self._active, TaskState.PAUSED,
                              f"interrupted by {owner}", self.now())
        dest = t["meta"].get("coords")
        self._paused = {"coords": dest, "dimension": t["meta"].get("dimension"),
                        "reason": owner_reason, "interrupted": time.monotonic()}
        self._active = None
        return {"ok": True, "interrupted_task": t["id"], "resumable": bool(dest),
                "raw": res}

    def resume_if_valid(self) -> dict:
        """Resume the paused objective only if it is still valid."""
        p = self._paused
        if p is None:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "nothing is paused"}}
        if not self.arbiter.request(MovementOwner.NAVIGATION):
            return {"ok": False, "error": {
                "code": ErrorCode.MOVEMENT_BUSY,
                "message": "another controller still owns movement"}}
        # validity re-check: named destinations must still exist
        if p.get("name") and self.registry.get(p["name"]) is None:
            self._paused = None
            return {"ok": False, "error": {
                "code": ErrorCode.LOCATION_NOT_FOUND,
                "message": "interrupted objective no longer exists"}}
        coords = p["coords"]
        # re-submitting a fresh goal is safe and cheap; Baritone replans
        out = self.navigate({"x": coords["x"], "y": coords["y"], "z": coords["z"],
                             "dimension": p["dimension"]})
        self._paused = None
        return out
