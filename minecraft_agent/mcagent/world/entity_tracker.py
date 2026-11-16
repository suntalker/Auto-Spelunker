"""Entity awareness (§ entity tracker): diff raw entity queries into
tracked records and hostile_detected / hostile_lost events.

Hostility is not guessed by name heuristics alone: the client may flag
`hostile` directly (Minescript entity data, simulation driver). The type
table below is only a fallback for clients that do not flag it.
"""
from __future__ import annotations
import math
from typing import List, Optional

from .events import EventQueue  # (unused import kept out) -- removed below
_KNOWN_HOSTILE_TYPES = {
    "minecraft:zombie", "minecraft:skeleton", "minecraft:spider",
    "minecraft:wolf", "minecraft:creeper", "minecraft:polar_bear",
    "minecraft:grouper", "minecraft:ghast", "minecraft:magma_cube",
    "minecraft:enderman", "minecraft:hoglin", "minecraft:piglin",
    "minecraft:warden", "minecraft:leaping", "minecraft:grizzled",
}
_KNOWN_PEACEFUL_TYPES = {
    "minecraft:villager", "minecraft:trader", "minecraft:merchant",
    "minecraft:horse", "minecraft:llama", "minecraft:fox", "minecraft:ocelot",
    "minecraft:panda", "minecraft:axolotl", "minecraft:rabbit", "minecraft:cat",
    "minecraft:dog", "minecraft:turtle", "minecraft:polar", "minecraft:warthog",
    "minecraft:elephant", "minecraft:bird", "minecraft:parrot", "minecraft:frog",
    "minecraft:human", "minecraft:player",
}


class EntityTracker:
    """Tracks entities between observations.

    update() is called with the raw (already bounded) result of
    WorldInterface.nearby_entities. It classifies, distances, and emits
    hostle_detected/hostile_lost events via the shared EventQueue.
    """

    def __init__(self, events: EventQueue, now_fn=None):
        self.events = events
        self.now_fn = now_fn  # test-injected clock; None -> time.monotonic
        self._tracked: dict = {}  # uuid -> record
        self._last_seen: dict = {}  # uuid -> observation timestamp

    def _now(self, provided):
        if provided is not None:
            return provided
        if self.now_fn is not None:
            return self.now_fn()
        import time as _time
        return _time.monotonic()

    def _classify(self, etype: str, flagged: Optional[bool]) -> bool:
        if flagged is True:
            return True
        if flagged is False:
            return False
        return etype in _KNOWN_HOSTILE_TYPES

    def update(self, raw, observed_at=None, player_pos=None) -> List[dict]:
        ts = self._now(observed_at)
        incoming = {}
        for e in raw.get("entities", []):
            uuid = e["uuid"]
            if not uuid:
                continue
            pos = e["position"]
            d = e.get("distance")
            if d is None and player_pos:
                d = round(math.sqrt(sum((pos[k] - player_pos[k]) ** 2 for k in ("x", "y", "z"))), 1)
            incoming[uuid] = {
                "uuid": uuid,
                "type": e["type"],
                "position": pos,
                "distance": d,
                "hostile": self._classify(e["type"], e.get("hostile")),
                "observed_at": ts,
            }

        for uuid, rec in incoming.items():
            prev = self._tracked.get(uuid)
            if prev is None or not prev.get("hostile"):
                if rec["hostile"]:
                    self.events.emit(
                        "hostile_detected",
                        {"uuid": uuid, "type": rec["type"],
                         "coordinates": rec["position"], "distance": rec["distance"]},
                        dedup_key=f"hostile:{uuid}:detected",
                        now=ts,
                    )
            self._tracked[uuid] = rec
            self._last_seen[uuid] = ts

        for uuid in list(self._tracked):
            if uuid not in incoming:
                rec = self._tracked.pop(uuid)
                if rec.get("hostile"):
                    self.events.emit(
                        "hostile_lost",
                        {"uuid": uuid, "type": rec["type"]},
                        dedup_key=f"hostile:{uuid}:lost",
                        now=ts,
                    )
        return [rec for rec in self._tracked.values()]

    def hostiles(self):
        return [r for r in self._tracked.values() if r.get("hostile")]

    def peaceful(self):
        return [r for r in self._tracked.values() if not r.get("hostile")]

    def snapshot(self):
        return list(self._tracked.values())
