"""Targeted resource discovery with spatial limits (§4 Tier-2/3, §5.1).

Rules honoured here:
 * every scan is bounded (radius clamped to config.tier3_max_radius)
 * cells scanned recently for the same block are skipped unless forced
 * deposits go to the resource_deposits table — never into the named-location
   registry (§5 note: separate registries)
 * freshness metadata: a deposit is 'candidate' until validate_deposit()
   re-scans it; 'depleted' when it no longer exists
 * exposure vocabulary: 'air_exposed' = a 6-neighbour position in the scan is
   air; 'visible' only when the client actually reported visibility.
   Reachability is NOT claimed here (§5: do not claim reachable from coords).
"""
from __future__ import annotations
import math
import time
from typing import List

from ..config import current_config
from ..enums import ErrorCode
from .world_model import WorldModel

AIR_NEIGHBORS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _same_block(name: str, want: str) -> bool:
    return name == want or name.split(":")[-1] == want.split(":")[-1]


def _clamp_radius(r: int) -> int:
    cfg = current_config()
    return max(1, min(int(r), cfg.tier2_radius))


def _cell(coord, size: int):
    return tuple(int(math.floor(c / size)) for c in coord)


class ResourceScanner:
    def __init__(self, iface, db, events, model: WorldModel):
        self.iface = iface
        self.db = db
        self.events = events
        self.model = model

    def _now(self) -> float:
        return time.monotonic()

    # ---- explored-area bookkeeping (avoid rescanning without reason) ----

    def _recently_scanned(self, center, block: str) -> bool:
        cfg = current_config()
        cx, _, cz = _cell((center["x"], center["y"], center["z"]), cfg.cell_size)
        row = self.db.conn.execute(
            "SELECT scanned_at FROM explored WHERE cell_x=? AND cell_z=? AND block=?",
            (cx, cz, block),
        ).fetchone()
        return row is not None and self._now() - row[0] < cfg.rescan_cooldown_s

    def _mark_scanned(self, center, block: str, radius: int):
        cfg = current_config()
        cx, _, cz = _cell((center["x"], center["y"], center["z"]), cfg.cell_size)
        self.db.conn.execute(
            "INSERT OR REPLACE INTO explored VALUES (:a,:b,:c,:d,:e,:f)",
            {"a": "minecraft:overworld", "b": cx, "c": cz, "d": block,
             "e": self._now(), "f": int(radius)},
        )
        self.db.conn.commit()

    # ---- bounded scans ----

    def scan(self, block: str, center=None, radius=None, allow_rescan=False) -> List[dict]:
        """Returns discovery records nearest-first, or [] if none/skip."""
        cfg = current_config()
        if center is None:
            px, py, pz = self.model.player_position()
            center = {"x": px, "y": py, "z": pz}
        radius = _clamp_radius(radius if radius is not None else cfg.default_scan_radius)
        if not allow_rescan and self._recently_scanned(center, block):
            return []

        res = self.iface.query_blocks(center, radius)
        self._mark_scanned(center, block, radius)
        solid = {(b["x"], b["y"], b["z"]) for b in res["blocks"]}
        now = self._now()
        found = []
        for b in res["blocks"]:
            if not _same_block(b["name"], block):
                continue
            exposure = "buried"
            if any((b["x"] + dx, b["y"] + dy, b["z"] + dz) not in solid
                   for dx, dy, dz in AIR_NEIGHBORS):
                exposure = "air_exposed"
            if b.get("visible"):
                exposure = "visible"
            found.append({
                "block": block,
                "coordinates": {"x": b["x"], "y": b["y"], "z": b["z"]},
                "exposure": exposure,
                "visible": bool(b.get("visible")),
                "distance": round(math.sqrt(sum((b[k] - center[k]) ** 2 for k in "xyz")), 1),
                "observed_at": now,
            })
            found.sort(key=lambda r: r["distance"])
        self.model.note_blocks(res["blocks"])
        if found:
            self._record_deposit(block, found, now)
            self.events.emit(
                "resource_discovered",
                {"block": block, "coordinates": found[0]["coordinates"],
                 "exposure": found[0]["exposure"], "count": len(found)},
                dedup_key=f"resource:{block}", now=now,
            )
        return found

    def _record_deposit(self, block: str, records: List[dict], now: float):
        cx = int(round(sum(r["coordinates"]["x"] for r in records) / len(records)))
        cy = int(round(sum(r["coordinates"]["y"] for r in records) / len(records)))
        cz = int(round(sum(r["coordinates"]["z"] for r in records) / len(records)))
        exposure = ("air_exposed" if any(r["exposure"] == "air_exposed" for r in records)
                    else "visible" if any(r["exposure"] == "visible" for r in records)
                    else "buried")
        same = self.db.conn.execute(
            "SELECT id FROM resource_deposits WHERE block=? AND x=? AND y=? AND z=?",
            (block, cx, cy, cz),
        ).fetchone()
        if same:
            self.db.conn.execute(
                "UPDATE resource_deposits SET last_verified = :ver WHERE id = :id",
                {"ver": now, "id": same[0]},
            )
        else:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO resource_deposits VALUES (:id,:b,:x,:y,:z,:d,:e,:q,:disc,:ver,:st)",
                {"id": None, "b": block, "x": cx, "y": cy, "z": cz,
                 "d": "minecraft:overworld", "e": exposure, "q": len(records),
                 "disc": now, "ver": now, "st": "candidate"},
            )
        self.db.conn.commit()

    def list_known(self, block=None, limit=25) -> List[dict]:
        q = ("SELECT id,block,x,y,z,dimension,exposure,quantity_estimate,"
             "discovered_at,last_verified,status FROM resource_deposits")
        args: list = []
        if block:
            q += " WHERE block=?"
            args.append(block)
        q += f" ORDER BY -last_verified LIMIT {int(limit)}"
        rows = self.db.conn.execute(q, args).fetchall()
        keys = ("id", "block", "x", "y", "z", "dimension", "exposure",
                "quantity_estimate", "discovered_at", "last_verified", "status")
        return [dict(zip(keys, r)) for r in rows]

    def validate_deposit(self, dep: dict) -> dict:
        """Small targeted re-scan around a stored deposit (§5.6 freshness)."""
        center = {"x": dep["x"], "y": dep["y"], "z": dep["z"]}
        res = self.iface.query_blocks(center, 3)
        present = [b for b in res["blocks"] if _same_block(b["name"], dep["block"])]
        now = self._now()
        status = "validated" if present else "depleted"
        self.db.conn.execute(
            "UPDATE resource_deposits SET last_verified = :now, status = :st WHERE id = :id",
            {"now": now, "st": status, "id": dep["id"]},
        )
        self.db.conn.commit()
        self.model.note_blocks(res["blocks"])
        return {"present": bool(present), "remaining": len(present),
                "exposure": "air_exposed" if present else None,
                "validated_at": now}
