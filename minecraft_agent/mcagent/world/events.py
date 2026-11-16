"""§8 Event-driven awareness.

An event queue so the LLM does not poll subsystems. Events carry metadata and
timestamps; emitting the same logical event twice within a throttle window is
suppressed (deduplication). The world model remains authoritative even if an
event is missed: events are notifications, not state.
"""
from __future__ import annotations
import time
import logging
from collections import deque
from typing import Optional

from ..enums import EVENT_NAMES  # documentation set; unknown names allowed but warned
from ..config import current_config


class EventQueue:
    def __init__(self, throttle_s: Optional[float] = None, max_pending: Optional[int] = None):
        cfg = current_config()
        self.throttle_s = throttle_s if throttle_s is not None else cfg.event_throttle_s
        self.max_pending = max_pending if max_pending is not None else cfg.max_pending_events
        self._pending: deque = deque()
        self._last_emit: dict = {}

    def emit(self, name: str, payload: Optional[dict] = None,
             dedup_key: Optional[str] = None, now: Optional[float] = None) -> bool:
        """Returns True when the event was actually queued."""
        if name not in EVENT_NAMES:
            # not fatal, but typos would otherwise silently multiply event kinds
            logging_warn(f"unknown event name {name}")
        now = time.monotonic() if now is None else now
        key = dedup_key or name
        last = self._last_emit.get(key)
        if last is not None and now - last < self.throttle_s:
            return False
        self._last_emit[key] = now
        event = {
            "event": name,
            "timestamp": now,
            "payload": payload or {},
        }
        self._pending.append(event)
        while len(self._pending) > self.max_pending:
            self._pending.popleft()
        return True

    def drain(self, limit: Optional[int] = None):
        out = []
        while self._pending and (limit is None or len(out) < limit):
            out.append(self._pending.popleft())
        return out


def logging_warn(msg: str):
    logging.getLogger("mcagent.world.events").warning(msg)
