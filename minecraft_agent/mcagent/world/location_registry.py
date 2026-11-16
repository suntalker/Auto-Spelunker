"""Persistent named-coordinate registry (§6).

The LLM can save/look up important places by name. Resource discoveries
live in a *separate* table (resource_deposits, see world/resource_scanner)
so the registries are not mixed.
"""
from __future__ import annotations
import time

from dataclasses import dataclass, asdict
from .database import valid_dimension
from ..enums import ErrorCode
from ..config import current_config

PROTECTED_CATEGORIES = {"base", "home"}  # accidental overwrite guard (§6)


@dataclass
class LocationRecord:
    name: str
    x: int
    y: int
    z: int
    dimension: str
    category: str = "other"
    description: str = ""
    protected: bool = False
    verified: bool = False
    discovered_at: float = 0.0
    last_verified: float = 0.0
    updated_at: float = 0.0


def _as_record(row) -> LocationRecord:
    name, x, y, z, dim, cat, desc, prot, ver, disc, lastv, upd = row
    return LocationRecord(name, x, y, z, dim, cat, desc, bool(prot), bool(ver), disc, lastv, upd)


def _row(rec: LocationRecord):
    return (rec.name, rec.x, rec.y, rec.z, rec.dimension, rec.category,
            rec.description, int(rec.protected), int(rec.verified),
            rec.discovered_at, rec.last_verified, rec.updated_at)


class LocationRegistry:
    def __init__(self, db, events):
        self.db = db
        self.events = events

    def _now(self):
        return time.monotonic()

    def save(self, name, x, y, z, dimension, category="other", description="",
             protected=None, verified=False, now=None) -> dict:
        """Save a new location. Refuses to silently overwrite an existing
        (especially important) location — callers must delete/update first."""
        if not name or not name.strip():
            return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS, "message": "empty name"}}
        for v in (x, y, z):
            if not isinstance(v, int) or isinstance(v, bool):
                return {"ok": False, "error": {"code": ErrorCode.INVALID_ARGS,
                                               "message": "coordinates must be integers"}}
        if not valid_dimension(dimension):
            return {"ok": False, "error": {"code": ErrorCode.INVALID_DIMENSION,
                                           "message": f"unknown dimension {dimension!r}"}}
        existing = self.get(name)
        if existing:
            important = existing.protected or existing.category in PROTECTED_CATEGORIES
            return {"ok": False, "error": {"code": ErrorCode.DUPLICATE_REQUEST,
                                           "message": f"{name} already saved; use update_location "
                                                      f"{'(overwrite=True required) ' if important else ''}to change it"}}
        rec = LocationRecord(name, int(x), int(y), int(z), dimension, category or "other",
                             description, bool(protected) if protected is not None else category in PROTECTED_CATEGORIES,
                             bool(verified), now or self._now(), 0.0, now or self._now())
        self.db.conn.execute(
            "INSERT INTO locations VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l)",
            dict(zip("abcdefghijkl", _row(rec))),
        )
        self.db.conn.commit()
        self.events.emit("location_saved", {"name": name, "category": category},
                         dedup_key=f"location:{name}")
        return {"ok": True, "record": asdict(rec)}

    def get(self, name) -> LocationRecord | None:
        row = self.db.conn.execute(
            "SELECT name,x,y,z,dimension,category,description,protected,verified,"
            "discovered_at,last_verified,updated_at FROM locations WHERE name=?",
            (name,),
        ).fetchone()
        return _as_record(row) if row else None

    def list(self, category=None, name_prefix=None, limit=None) -> list:
        cfg_limit = 50
        limit = limit or cfg_limit
        q = ("SELECT name,x,y,z,dimension,category,description,protected,verified,"
             "discovered_at,last_verified,updated_at FROM locations")
        clauses, args = [], []
        if category:
            clauses.append("category=?"); args.append(category)
        if name_prefix:
            clauses.append("name>=?"); args.append(name_prefix)
            args.append(name_prefix + "\uffff")  # prefix upper bound
        sql = q + (" WHERE (" + " OR ".join(clauses) + ")" if clauses else "")
        sql += " ORDER BY name"
        sql += f" LIMIT {int(limit)}"
        rows = self.db.conn.execute(sql, args).fetchall() if clauses else self.db.conn.execute(sql).fetchall()
        return [asdict(_as_record(r)) for r in rows]

    def search(self, query: str, limit=25) -> list:
        like = f"%{query}%"
        rows = self.db.conn.execute(
            "SELECT name,x,y,z,dimension,category,description FROM locations "
            "WHERE (name LIKE ? OR category LIKE ? OR description LIKE ?) "
            f"LIMIT {int(limit)}",
            (like, like, like),
        ).fetchall()
        return [
            {"name": r[0], "coordinates": {"x": r[1], "y": r[2], "z": r[3]},
             "dimension": r[4], "category": r[5], "description": r[6]}
            for r in rows
        ]

    def update(self, name, x=None, y=None, z=None, description=None, verified=None,
               now=None, overwrite=False) -> dict:
        rec = self.get(name)
        if rec is None:
            return {"ok": False, "error": {"code": ErrorCode.LOCATION_NOT_FOUND,
                                           "message": f"no location named {name}"}}
        important = rec.protected or rec.category in PROTECTED_CATEGORIES
        if important and not overwrite:
            return {"ok": False, "error": {
                "code": ErrorCode.DUPLICATE_REQUEST,
                "message": f"{name} is an important location; "
                           "pass overwrite=True to change it"}}
        upd = rec
        if x is not None:
            upd.x = int(x)
        if y is not None:
            upd.y = int(y)
        if z is not None:
            upd.z = int(z)
        if description is not None:
            upd.description = description
        if verified is not None:
            upd.verified = bool(verified)
            if upd.verified:
                upd.last_verified = now or self._now()
        upd.updated_at = now or self._now()
        self.db.conn.execute(
            "INSERT OR REPLACE INTO locations VALUES (:a,:b,:c,:d,:e,:f,:g,:h,:i,:j,:k,:l)",
            dict(zip("abcdefghijkl", _row(upd))),
        )
        self.db.conn.commit()
        return {"ok": True, "record": asdict(upd)}

    def delete(self, name, force=False) -> dict:
        rec = self.get(name)
        if rec is None:
            return {"ok": False, "error": {"code": ErrorCode.LOCATION_NOT_FOUND,
                                           "message": f"no location named {name}"}}
        important = rec.protected or rec.category in PROTECTED_CATEGORIES
        if important and not force:
            return {"ok": False, "error": {"code": ErrorCode.DUPLICATE_REQUEST,
                                           "message": f"{name} is an important location; "
                                                      "pass force=True to delete deliberately"}}
        self.db.conn.execute("DELETE FROM locations WHERE name=?", (name,))
        self.db.conn.commit()
        return {"ok": True, "deleted": name, "important": important}
