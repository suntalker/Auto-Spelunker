"""Long-running task bookkeeping shared by navigation and mining (§7/§12).

Every operation gets a unique request/task id; states transition through the
TaskState enum only (logging of task transitions, §10). Completion is never
inferred from "submitted" — it requires a backend confirmation (§5.6/§9).
"""
from __future__ import annotations
import time

from ..enums import TaskState
from ..config import current_config


class TaskTracker:
    def __init__(self, db):
        self.db = db
        self._seq = 0
        self._tasks: dict = {}  # task_id -> record

    def new(self, kind: str, destination: str, meta: dict | None = None,
            now: float | None = None) -> str:
        prefix = "nav_" if kind in ("navigate", "resume") else "mine_"
        self._seq += 1
        task_id = f"{prefix}{self._seq:03d}"
        now = now or time.monotonic()
        self._tasks[task_id] = {
            "id": task_id,
            "kind": kind,
            "destination": destination,
            "state": TaskState.ACCEPTED,
            "created": now,
            "meta": dict(meta or {}),
        }
        self._log(task_id, kind, destination, TaskState.ACCEPTED, "submitted", now)
        return task_id

    def get(self, task_id: str) -> dict | None:
        return self._tasks.get(task_id)

    def transition(self, task_id: str, state, reason: str = "", now=None):
        t = self._tasks.get(task_id)
        if t is None:
            return False
        now = now or time.monotonic()
        t["state"] = state
        t["updated"] = now
        self._log(task_id, t["kind"], t["destination"], state, reason, now)
        return True

    def active(self):
        live = {TaskState.ACCEPTED, TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.PAUSED}
        return [t for t in self._tasks.values() if t["state"] in live]

    def _log(self, task_id, kind, destination, state, reason, at):
        self.db.log_task(task_id, kind, destination, state.value, reason, at)
