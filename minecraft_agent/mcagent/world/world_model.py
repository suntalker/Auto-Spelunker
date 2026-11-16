"""Layer B — structured world model (§3 Layer B, §4 tiered awareness).

Converts raw observations into structured, filtered, freshness-qualified
state. It distinguishes (KnowledgeState):

  OBSERVED   from a recent observation (age < observe_max_age_s)
  KNOWN      previously discovered, stored in world memory (db)
  INFERRED   estimated from observations (explicitly tagged by producers)
  UNKNOWN    not established — never presented as fact

Never presents stale or inferred information as confirmed current state.
"""
from __future__ import annotations
import math
import time
from typing import Optional

from ..enums import KnowledgeState, NavState, MovementOwner
from ..config import current_config


class _Clock:
    def now(self) -> float:
        return time.monotonic()


class WorldModel:
    def __init__(self, interface, db, events, entity_tracker):
        self.iface = interface
        self.db = db
        self.events = events
        self.tracker = entity_tracker
        self._now_real = _Clock().now
        self._player: dict | None = None       # last player snapshot
        self._player_at: float = 0.0
        self._last_passive: float = 0.0
        self._scan_observations: dict = {}     # (x,y,z) -> {"observed_at", "name", "exposure"}
        self._observations_age: float = 0.0    # ts of last block observation set

    def now(self) -> float:
        return self._now_real()

    def classify_age(self, ts: Optional[float]) -> KnowledgeState:
        cfg = current_config()
        if ts is None:
            return KnowledgeState.UNKNOWN
        if self.now() - ts <= cfg.observe_max_age_s:
            return KnowledgeState.OBSERVED
        return KnowledgeState.KNOWN

    # ---------------- player state ----------------

    def player_status(self, force: bool = False) -> dict:
        """Current player state; re-observes only if the cache is stale."""
        cfg = current_config()
        if self._player is None or force or self.now() - self._player_at > cfg.observe_max_age_s:
            snap = self.iface.player_state()
            self._player = snap
            self._player_at = self.now()
        age = self.now() - self._player_at
        return {
            "position": self._player["position"],
            "dimension": self._player.get("dimension"),
            "health": self._player.get("health"),
            "hunger": self._player.get("hunger"),
            "alive": self._player.get("alive", True),
            "environment": self._player.get("environment", {}),
            "biome": self._player.get("biome"),  # informational; never invented
            "freshness": "observed" if age <= cfg.observe_max_age_s else "stale",
        }

    def survival_events(self):
        """Emit health_critical / hunger_critical when below thresholds."""
        p = self._player or {}
        cfg = current_config()
        if p.get("health") is not None and p["health"] <= cfg.health_critical:
            self.events.emit("health_critical", {"health": p["health"],
                                                 "coordinates": p.get("position")},
                             dedup_key="survival:health", now=self.now())
        if p.get("hunger") is not None and p["hunger"] <= cfg.hunger_critical:
            self.events.emit("hunger_critical", {"hunger": p["hunger"]},
                             dedup_key="survival:hunger", now=self.now())

    # ---------------- tiered awareness ----------------

    def passive_observe(self, force: bool = False) -> bool:
        """Tier-1: player state + bounded entity query + hazard check.

        Skipped (returns False) when a sweep already ran within
        passive_interval_s, keeping observation frequency cheap (§10).
        """
        cfg = current_config()
        if not force and self.now() - self._last_passive < cfg.passive_interval_s:
            return False
        self._last_passive = self.now()
        self.player_status(force=True)
        raw = self.iface.nearby_entities(cfg.entity_radius)
        self.tracker.update(raw, observed_at=self.now(),
                            player_pos=self._player["position"])
        env = self._player.get("environment", {})
        if env.get("raining") or env.get("thundering") or env.get("is_night"):
            self.events.emit("environmental_hazard_detected",
                             {"environment": env, "coordinates": self._player["position"]},
                             dedup_key="env:hazard", now=self.now())
        self.survival_events()
        return True

    def note_blocks(self, blocks, observed_at=None):
        """Record that these block coordinates were actually observed now."""
        ts = observed_at or self.now()
        for b in blocks:
            self._scan_observations[(b["x"], b["y"], b["z"])] = {
                "name": b["name"], "observed_at": ts,
                "exposure": b.get("exposure", "visible" if b.get("visible") else "buried"),
            }
        self._observations_age = ts

    def block_knowledge(self, x, y, z) -> tuple:
        """(KnowledgeState, detail) for one coordinate."""
        obs = self._scan_observations.get((x, y, z))
        if obs and self.now() - obs["observed_at"] <= current_config().observe_max_age_s:
            return KnowledgeState.OBSERVED, obs
        row = self.db.conn.execute(
            "SELECT block,x,y,z,dimension,exposure,status,last_verified "
            "FROM resource_deposits WHERE x=? AND y=? AND z=?",
            (x, y, z),
        ).fetchone()
        if row:
            return KnowledgeState.KNOWN, {
                "name": row[0], "exposure": row[5], "status": row[6],
                "last_verified": row[7],
            }
        return KnowledgeState.UNKNOWN, None

    def player_position(self):
        """Player position via the cached-snapshot logic (refreshes only
        when the cache is stale, §observe_max_age_s). Never invents (0,0,0)
        silently: if the player was never observed, callers get an error."""
        self.player_status()
        p = (self._player or {}).get("position")
        if p is None:
            raise ValueError("player never observed (no player snapshot)")
        return (p["x"], p["y"], p["z"])

    def distance_to(self, coords) -> float:
        p = self.player_position()
        return round(math.sqrt(sum((p[i] - coords[i]) ** 2 for i in range(3))), 1)

    def nearby_entities(self, radius=None):
        cfg = current_config()
        radius = radius or cfg.entity_radius
        raw = self.iface.nearby_entities(min(radius, 128))
        self.tracker.update(raw, observed_at=self.now(),
                            player_pos=self.player_position())
        return self.tracker.snapshot()
