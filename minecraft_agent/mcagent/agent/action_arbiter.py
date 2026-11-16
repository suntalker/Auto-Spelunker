"""Centralized movement ownership (§9).

One place decides which subsystem may issue movement. Combat (and any other
controller) lives in its own module; this arbiter only arbitrates. When
ownership changes, inputs held by the previous owner are released through
the backend, and callers that were interrupted must re-request explicitly —
resuming is the manager's job (it validates the objective first).
"""
from __future__ import annotations
import time

from ..enums import MovementOwner


class ActionArbiter:
    def __init__(self, iface, events, model=None):
        self.iface = iface
        self.events = events
        self.model = model
        self.owner = None  # type: MovementOwner | None
        self._reason = ""
        self._since = 0.0

    def now(self) -> float:
        return time.monotonic()

    def request(self, owner, reason: str = "") -> bool:
        """Ask for movement ownership. Succeeds when free, already owned
        (same owner), or when EMERGENCY takes over anything (§9 priority)."""
        owner = MovementOwner(owner)  # accept plain strings too
        if self.owner is None or self.owner == owner:
            self._adopt(owner, reason)
            return True
        if owner == MovementOwner.EMERGENCY:
            self.force(owner, reason)
            return True
        return False

    def _adopt(self, owner, reason: str = "") -> None:
        if self.owner is not None and self.owner != owner:
            self.events.emit("ownership_changed",
                             {"from": self.owner.value, "to": owner.value,
                              "reason": reason},
                             now=self.now())
        elif self.owner is None:
            self.events.emit("ownership_changed",
                             {"from": None, "to": owner.value, "reason": reason},
                             now=self.now())
        self.owner = owner
        self._reason = reason
        self._since = self.now()

    def force(self, owner, reason: str = "") -> dict:
        """Override the current owner (combat interrupting navigation,
        emergency overriding anything). Releases the previous owner's inputs."""
        owner = MovementOwner(owner)
        previous = self.owner
        if previous == owner:
            return {"ok": True, "owner": owner.value, "changed": False}
        released = {"released": False}
        if previous is not None:
            try:
                released = self.iface.release_inputs()
            except Exception as exc:  # client may be mid-disconnect
                released = {"released": False, "error": str(exc)}
        self.owner = owner
        self._reason = reason
        self._since = self.now()
        self.events.emit(
            "ownership_changed",
            {"from": previous.value if previous else None,
             "to": owner.value, "reason": reason},
            now=self.now(),
        )
        return {"ok": True, "owner": owner.value, "changed": True,
                "previous": previous.value if previous else None,
                "released_inputs": bool(released.get("released", True))}

    def release(self, owner) -> bool:
        """Give up ownership (e.g. combat finished, release movement)."""
        owner = MovementOwner(owner)
        if self.owner is not None and self.owner != owner:
            return False
        prev = self.owner
        if prev is not None:
            try:
                self.iface.release_inputs()
            except Exception:
                pass
            self.events.emit("ownership_changed",
                             {"from": prev.value, "to": None, "reason": "released"},
                             now=self.now())
        self.owner = None
        self._reason = ""
        return True

    def status(self) -> dict:
        return {"owner": self.owner.value if self.owner else None,
                "reason": self._reason,
                "since": self._since}
