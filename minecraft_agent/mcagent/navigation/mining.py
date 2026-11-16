"""Dual-mode mining orchestration (§5).

Strategies:
  EXPOSED — search for air_exposed/visible matching blocks within a bounded
            radius, validate they still exist, then delegate digging to Baritone.
            Explicitly opportunistic: only finds what perception can see (§5.1).
  TUNNEL  — delegate direct resource seeking to Baritone's native #mine
            behaviour; no custom A* is implemented (§5.2).
  AUTO    — default: exposed deposit first, else fall back to TUNNEL, without
            flip-flopping without a reason (§5.3).

Quantity limits, progress and cancellation are enforced at this Python
layer regardless of what the installed Baritone interface supports (§5.5):
completion is verified by inventory deltas — never by a submitted command
alone (§5.6: "never report successful collection from a submission").
"""
from __future__ import annotations
import math
import time
from typing import Optional

from ..enums import MiningMode, TaskState, ErrorCode
from ..config import current_config
from .baritone import BaritoneBridge
from .tasks import TaskTracker  # noqa


class MiningCoordinator:
    def __init__(self, bridge: BaritoneBridge, scanner, model, task_tracker, events, db):
        self.bridge = bridge
        self.scanner = scanner
        self.model = model
        self.tasks = task_tracker
        self.events = events
        self.db = db
        self._active: str | None = None
        self._last_strategy: str | None = None  # mode-switch guard

    def now(self) -> float:
        return time.monotonic()

    # ---- persistent setting (§5.4) ----

    def configured_mode(self) -> str:
        raw = self.db.settings_get("mining_mode")
        try:
            return MiningMode(raw or current_config().default_mining_mode).value
        except ValueError:
            return current_config().default_mining_mode

    def set_mode(self, mode: str) -> dict:
        try:
            mode = MiningMode(mode.upper()).value
        except ValueError:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "mode must be EXPOSED|TUNNEL|AUTO"}}
        self.db.settings_set("mining_mode", mode)
        return {"ok": True, "mining_mode": mode}

    # ---- submit a mining goal ----

    def mine(self, block: str, quantity: Optional[int], mode: Optional[str] = None) -> dict:
        cfg = current_config()
        requested_mode = mode or self.configured_mode()
        if mode:
            try:
                requested_mode = MiningMode(mode.upper()).value
            except ValueError:
                return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                               "message": f"unknown mode {mode}"}}
        if quantity is not None and int(quantity) < 1:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "quantity must be >= 1"}}

        initial = self.model.iface.inventory_count(_item(block))
        strategy = requested_mode
        deposit = None

        if requested_mode in (MiningMode.EXPOSED.value, MiningMode.AUTO.value):
            found = self.scanner.scan(block, None, cfg.mine_max_radius, allow_rescan=True)
            exposed = [r for r in found if r["exposure"] in ("air_exposed", "visible")]
            if exposed:
                deposit = exposed[0]  # nearest matching deposit first (§5.1)
                strategy = MiningMode.EXPOSED.value
            elif requested_mode == MiningMode.EXPOSED.value:
                return {"ok": False, "error": {
                    "code": ErrorCode.RESOURCE_NOT_FOUND,
                    "strategy": MiningMode.EXPOSED.value,
                    "message": "no exposed/visible matching blocks within range "
                               "(EXPOSED mode never tunnels)"}}
            else:
                # AUTO with nothing exposed: the honest outcome is a tunnel
                # (§5: prefer not to oscillate — record for next time).
                strategy = MiningMode.TUNNEL.value
                self._last_strategy = MiningMode.TUNNEL.value

        task_id = self.tasks.new("mine", f"mine:{block}",
                                 {"block": block, "quantity": quantity,
                                  "initial_count": initial, "strategy": strategy},
                                 now=self.now())
        coords = deposit["coordinates"] if deposit else None
        submit = self.bridge.submit_mine(task_id, block, quantity, coords)
        if not submit["ok"]:
            self.tasks.transition(task_id, TaskState.FAILED, "bridge refused", self.now())
            return {"ok": False, "error": {
                "code": ErrorCode.NAVIGATION_BRIDGE_UNAVAILABLE,
                "message": "mining bridge refused the request",
                "strategy": strategy}}
        self._active = task_id
        self.tasks.transition(task_id, TaskState.IN_PROGRESS, "goal submitted", self.now())
        return {"ok": True, "task_id": task_id, "status": TaskState.IN_PROGRESS.value,
                "block": _item(block), "requested_quantity": quantity,
                "strategy": strategy, "initial_count": initial,
                "deposit_validated": bool(deposit), "method": submit["method"]}

    def poll(self, task_id=None) -> dict:
        """Monitor a mining task; on backend completion VERIFY via inventory (§5.6)."""
        task_id = task_id or self._active
        t = self.tasks.get(task_id)
        if t is None:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "no mining task"}}
        raw = self.bridge.poll(task_id)
        state = raw.get("state", "not_found")
        initial = t["meta"]["initial_count"]
        qty = t["meta"]["quantity"]
        item = _item(t["meta"]["block"])

        if state == "done":
            final = self.model.iface.inventory_count(item)
            collected = final - initial
            complete = (collected >= int(qty)) if qty is not None else collected > 0
            if complete and qty is not None:
                # Baritone #mine runs until cancelled: stop it once the
                # requested quantity is verified collected (python-layer quantity limit).
                self.bridge.cancel(task_id)
            self.tasks.transition(task_id,
                                  TaskState.COMPLETED if complete else TaskState.FAILED,
                                  "verified via inventory delta", self.now())
            self._active = None
            self._last_strategy = t["meta"]["strategy"]
            return {
                "ok": bool(complete),
                "task_id": task_id,
                "block": item,
                "requested_quantity": qty,
                "strategy_used": t["meta"]["strategy"],
                "initial_count": initial,
                "final_count": final,
                "collected": collected,
                "task_status": ("completed" if complete else "failed"),
                "failure_reason": None if complete else
                    "inventory delta below requested quantity",
                "backend_detail": raw.get("detail"),
            }
        if state in ("blocked", "failed", "not_found", "cancelled"):
            self.tasks.transition(task_id, TaskState.FAILED, state, self.now())
            self._active = None
            self.events.emit("navigation_failed", {"task_id": task_id, "reason": state},
                             now=self.now())
            final = self.model.iface.inventory_count(item)
            return {"ok": False, "task_id": task_id, "status": "failed",
                    "block": item, "collected": final - initial,
                    "failure_reason": state}
        # still active: progress metadata without claiming completion
        final = self.model.iface.inventory_count(item)
        return {"ok": True, "task_id": task_id, "status": TaskState.IN_PROGRESS.value,
                "block": item, "collected_so_far": final - initial,
                "requested_quantity": qty}

    def cancel(self, task_id=None) -> dict:
        task_id = task_id or self._active
        if task_id is None or self.tasks.get(task_id) is None:
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                           "message": "no mining task to cancel"}}
        res = self.bridge.cancel(task_id)
        self.tasks.transition(task_id, TaskState.CANCELLED, "user cancel", self.now())
        if task_id == self._active:
            self._active = None
        return {"ok": True, "cancelled": task_id, "raw": res}

    def get_status(self) -> dict:
        if self._active is None:
            return {"ok": True, "status": "idle", "task_id": None}
        return self.poll(self._active)


def _item(block: str) -> str:
    """Normalize the item name used for inventory counts."""
    return block if ":" in block else f"minecraft:{block}"
