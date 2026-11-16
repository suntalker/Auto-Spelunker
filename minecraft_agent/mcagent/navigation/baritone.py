"""Baritone submission layer (§5/§7).

Two submission paths, chosen once per backend by capability detection:

  native — Pyritone (or the simulation driver) exposes navigate/mine/
           cancel_task operations directly. Verified at runtime; we do not
           invent Pyritone method names (see bridge.drivers.PyritoneDriver).

  chat   — direct Baritone console commands typed into the client:
           #goto x y z, #mine <block> [quantity]. Baritone IS installed in
           the user's game (meteor jar + baritone console). The exact #mine
           quantity syntax is UNVERIFIED offline; quantity limits are
           therefore additionally enforced by the Python layer (mining.py
           watches inventory and cancels the task).

Command templates come from config so syntax stays editable without touching
logic. Baritone owns pathfinding and digging; this layer only submits goals
and cancels them.
"""
from __future__ import annotations
import time

from ..enums import NavState, TaskState
from ..config import current_config


class BaritoneBridge:
    def __init__(self, iface, task_tracker, events, describe_result: dict):
        self.iface = iface
        self.tasks = task_tracker
        self.events = events
        caps = set((describe_result or {}).get("capabilities", []))
        self.native = {"navigate", "mine", "cancel_task"} <= caps

    def method(self) -> str:
        return "native" if self.native else "chat"

    def submit_navigate(self, task_id: str, coords: dict, dimension: str) -> dict:
        if self.native:
            res = self.iface.navigate(coords, dimension, task_id)
            ok = bool(res.get("accepted", False))
            return {"ok": ok, "method": "native", "task_id": task_id,
                    "raw": res}
        cmd = current_config().baritone_goto_template.format(
            x=int(coords["x"]), y=int(coords["y"]), z=int(coords["z"]))
        res = self.iface.chat_command(cmd, task_id)
        ok = bool(res.get("accepted", False))
        return {"ok": ok, "method": "chat", "task_id": res.get("task_id", task_id),
                "command": cmd, "raw": res}

    def submit_mine(self, task_id: str, block: str, quantity: int | None,
                    coords: dict | None) -> dict:
        if self.native:
            res = self.iface.mine(block, quantity, task_id, coords)
            return {"ok": bool(res.get("accepted", False)), "method": "native",
                    "task_id": task_id, "raw": res}
        cmd = current_config().baritone_mine_template.format(block=block)
        # Baritone v1.15.0 `#mine <block>` takes no quantity argument (verified
        # against pyritone's bundled COMMAND_SPECS) — quantity control lives in
        # mining.py (inventory watch + cancel when satisfied).
        res = self.iface.chat_command(cmd, task_id)
        return {"ok": bool(res.get("accepted", False)), "method": "chat",
                "task_id": res.get("task_id", task_id), "command": cmd, "raw": res}

    def cancel(self, task_id: str) -> dict:
        res = self.iface.cancel_task(task_id)
        ok = bool(res.get("cancelled", False))
        return {"ok": ok, "raw": res}

    def poll(self, task_id: str) -> dict:
        return self.iface.task_status(task_id)
