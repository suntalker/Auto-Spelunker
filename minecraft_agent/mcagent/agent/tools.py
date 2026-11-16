"""Layer C — LLM-facing tool surface (§4/§5/§7/§9/§10/§11).

Each tool validates its arguments, calls into the subsystems, and returns a
structured result. Business logic lives in the subsystems; this module is a
thin, explicit surface. No arbitrary Python execution, no raw block dumps:
`look()` is a summary and drill-down tools (inspect_area, get_nearby_
entities, search_world_memory) provide detail on demand (§10).

Combat tools are registered but return NOT_IMPLEMENTED unless a separate
combat module has registered its controller with the adapter — combat
behaviour is never implemented here.
"""
from __future__ import annotations
import math
import time

from ..enums import KnowledgeState, ErrorCode, NavState
from ..config import current_config


class WorldTools:
    def __init__(self, core):
        self.core = core  # MinecraftAgentAdapter instance
        self._seen_requests: dict = {}  # idempotency guard for duplicate calls

    # ---------- schemas (also mirrored in the Pi extension) ----------

    def schemas(self) -> dict:
        num = {"type": "integer"}
        return {
            "look": {"type": "object", "properties": {}},
            "get_status": {"type": "object", "properties": {}},
            "get_player_status": {"type": "object", "properties": {}},
            "inspect_area": {"type": "object", "properties": {
                "x": num, "y": num, "z": num,
                "radius": {"type": "integer", "minimum": 1}}},
            "find_resource": {"type": "object", "properties": {
                "block_type": {"type": "string"},
                "radius": {"type": "integer", "minimum": 1}}},
            "get_nearby_entities": {"type": "object", "properties": {
                "radius": {"type": "integer", "minimum": 1}}},
            "navigate": {"type": "object", "properties": {
                "destination": {"type": ["string", "object"]}}},
            "mine": {"type": "object", "properties": {
                "block_type": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1},
                "mode": {"type": "string", "enum": ["EXPOSED", "TUNNEL", "AUTO"]}}},
            "get_navigation_status": {"type": "object", "properties": {}},
            "get_mining_status": {"type": "object", "properties": {}},
            "cancel_action": {"type": "object", "properties": {}},
            "remember_location": {"type": "object", "properties": {
                "name": {"type": "string"},
                "x": num, "y": num, "z": num,
                "dimension": {"type": "string"},
                "category": {"type": "string"},
                "description": {"type": "string"}}},
            "get_location": {"type": "object", "properties": {"name": {"type": "string"}}},
            "update_location": {"type": "object", "properties": {
                "name": {"type": "string"}, "x": num, "y": num, "z": num,
                "overwrite": {"type": "boolean"}}},
            "list_locations": {"type": "object", "properties": {
                "category": {"type": "string"}}},
            "forget_location": {"type": "object", "properties": {
                "name": {"type": "string"}, "force": {"type": "boolean"}}},
            "search_world_memory": {"type": "object", "properties": {
                "query": {"type": "string"}}},
            "get_mining_mode": {"type": "object", "properties": {}},
            "set_mining_mode": {"type": "object", "properties": {
                "mode": {"type": "string", "enum": ["EXPOSED", "TUNNEL", "AUTO"]}}},
            "get_events": {"type": "object", "properties": {
                "limit": {"type": "integer", "minimum": 1}}},
            # combat tools: present, but they delegate to an external module only
            "engage_target": {"type": "object", "properties": {
                "target_uuid": {"type": "string"}}},
            "disengage": {"type": "object", "properties": {}},
            "get_combat_status": {"type": "object", "properties": {}},
        }

    def required(self, name):
        req = {
            "inspect_area": ["x", "y", "z"],
            "find_resource": ["block_type"],
            "navigate": ["destination"],
            "mine": ["block_type"],
            "remember_location": ["name", "x", "y", "z"],
            "get_location": ["name"],
            "update_location": ["name"],
            "forget_location": ["name"],
            "search_world_memory": ["query"],
            "set_mining_mode": ["mode"],
            "engage_target": ["target_uuid"],
        }
        return req.get(name, [])

    # ---------- validation helpers ----------

    def _err(self, code, message):
        return {"ok": False, "error": {"code": code.value if hasattr(code, "value") else code,
                                        "message": message}}

    def _validate(self, name, args):
        missing = [k for k in self.required(name) if k not in (args or {})]
        if missing:
            return self._err(ErrorCode.INVALID_ARGS, f"missing argument(s) {missing}")
        spec = self.schemas().get(name, {}).get("properties", {})
        for key, val in (args or {}).items():
            if key not in spec:
                return self._err(ErrorCode.INVALID_ARGS, f"unknown argument {key}")
        for key in ("x", "y", "z", "radius", "quantity"):
            if key in args:
                if isinstance(args[key], bool) or not isinstance(args[key], (int, float)):
                    return self._err(ErrorCode.INVALID_ARGS, f"{key} must be a number")
        return None

    # ---------- handlers ----------

    def look(self, _args) -> dict:
        """Concise surroundings summary (§10 example shape). Runs the cheap
        Tier-1 awareness pass first so the snapshot reflects real
        observations, not stale memory."""
        c = self.core
        c.perception.passive_observe(force=True)
        player = c.model.player_status(force=False)
        hostiles = c.model.tracker.hostiles()
        resources = c.scanner.list_known(limit=5)
        landmarks = c.registry.list(category="landmark")[:5]
        return {
            "ok": True,
            "player": {
                "position": list(player["position"].values()) if player["position"] else None,
                "position_map": player["position"],
                "dimension": player["dimension"],
                "health": player["health"],
                "hunger": player["hunger"],
                "freshness": player["freshness"],
            },
            "nearby": {
                "hostiles": len(hostiles),
                "hostile_sample": [
                    {"type": h["type"], "distance": h.get("distance")} for h in hostiles[:3]],
                "known_resources": [
                    {"block": r["block"], "exposure": r["exposure"], "knowledge":
                        KnowledgeState.KNOWN.value} for r in resources],
                "landmarks": [l["name"] for l in landmarks],
            },
            "activity": {
                "navigation": (c.nav.get_navigation_status() if c.nav._active else
                               {"state": NavState.IDLE.value})["state"],
                "mining": "active" if c.mining._active else "idle",
                "movement_owner": c.arbiter.status()["owner"],
            },
            "pending_events": c.events.drain(),
        }

    def get_status(self, _args) -> dict:
        c = self.core
        player = c.model.player_status()
        return {"ok": True,
                "player": {"position": player["position"], "health": player["health"],
                           "hunger": player["hunger"], "dimension": player["dimension"],
                           "freshness": player["freshness"]},
                "activity": {"navigation": c.nav.get_navigation_status(),
                             "mining": c.mining.get_status(),
                             "movement_owner": c.arbiter.status()},
                "backend": c.iface.describe(),
                "observed_at": time.monotonic()}

    def get_player_status(self, args) -> dict:
        player = self.core.model.player_status(force=True)
        return {"ok": True, "player": player}

    def inspect_area(self, args) -> dict:
        c = self.core
        center = {k: int(args[k]) for k in "xyz"}
        radius = int(args.get("radius", current_config().default_scan_radius))
        if radius > current_config().tier3_max_radius:
            return self._err(ErrorCode.INVALID_ARGS,
                             f"radius limited to {current_config().tier3_max_radius}")
        out = c.perception.inspect_area(center, radius)
        return {"ok": True, **out}

    def find_resource(self, args) -> dict:
        c = self.core
        found = c.perception.objective_find_resource(
            args["block_type"], args.get("radius"))
        return {"ok": True, "found": bool(found), "results": found,
                "note": ("empty result: none in range, or area recently scanned"
                         if not found else "")}

    def get_nearby_entities(self, args) -> dict:
        radius = int(args.get("radius", current_config().entity_radius))
        if radius > 128:
            return self._err(ErrorCode.INVALID_ARGS, "radius limited to 128")
        ents = self.core.model.nearby_entities(radius)
        return {"ok": True, "entities": ents}

    def navigate(self, args) -> dict:
        return self.core.nav.navigate(args["destination"])

    def mine(self, args) -> dict:
        c = self.core
        return c.mining.mine(args["block_type"], args.get("quantity"), args.get("mode"))

    def get_navigation_status(self, _args) -> dict:
        return {"ok": True, **self.core.nav.get_navigation_status()}

    def get_mining_status(self, _args) -> dict:
        return {"ok": True, **self.core.mining.get_status()}

    def cancel_action(self, _args) -> dict:
        c = self.core
        if c.nav._active:
            return c.nav.cancel()
        if c.mining._active:
            return c.mining.cancel()
        return self._err(ErrorCode.INVALID_ARGS, "nothing to cancel")

    def remember_location(self, args) -> dict:
        c = self.core
        out = c.registry.save(args["name"], args["x"], args["y"], args["z"],
                              args.get("dimension", "minecraft:overworld"),
                              args.get("category", "other"),
                              args.get("description", ""))
        return out

    def get_location(self, args) -> dict:
        rec = self.core.registry.get(args["name"])
        if rec is None:
            return self._err(ErrorCode.LOCATION_NOT_FOUND,
                             f"no saved location named {args['name']}")
        return {"ok": True, "record": rec.__dict__}

    def update_location(self, args) -> dict:
        return self.core.registry.update(args["name"], args.get("x"), args.get("y"),
                                        args.get("z"), args.get("description"),
                                        None, None, bool(args.get("overwrite", False)))

    def list_locations(self, args) -> dict:
        return {"ok": True,
                "locations": self.core.registry.list(category=args.get("category"),
                                                    name_prefix=None)}

    def forget_location(self, args) -> dict:
        return self.core.registry.delete(args["name"], force=bool(args.get("force")))

    def search_world_memory(self, args) -> dict:
        c = self.core
        locations = c.registry.search(args["query"])
        resources = c.scanner.list_known(block=args["query"])
        # also substring block search over deposits
        like = f"%{args['query']}%"
        rows = c.scanner.db.conn.execute(
            "SELECT id,block,x,y,z,exposure,status,last_verified "
            "FROM resource_deposits WHERE block LIKE ? LIMIT 25", (like,)).fetchall()
        by_id = {r["id"]: r for r in resources}
        for row in rows:
            if row[0] not in by_id:
                resources.append({"id": row[0], "block": row[1], "x": row[2], "y": row[3],
                                  "z": row[4], "exposure": row[5], "status": row[6],
                                  "last_verified": row[7]})
        return {"ok": True, "locations": locations, "deposits": resources,
                "knowledge": KnowledgeState.KNOWN.value,
                "note": "memory results are KNOWN, not current observations; "
                        "use validate_deposit flow via find_resource to confirm"}

    def get_mining_mode(self, _args) -> dict:
        return {"ok": True, "mining_mode": self.core.mining.configured_mode()}

    def set_mining_mode(self, args) -> dict:
        return self.core.mining.set_mode(args["mode"])

    def get_events(self, args) -> dict:
        limit = int(args.get("limit", 16))
        return {"ok": True, "events": self.core.events.drain(limit)}

    # ---------- combat delegation (external module only) ----------

    def engage_target(self, args) -> dict:
        controller = self.core.combat_controller
        if controller is None:
            return self._err(ErrorCode.NOT_IMPLEMENTED,
                             "no combat module registered; combat is a separate subsystem")
        return {"ok": True, **controller("engage", args)}

    def disengage(self, _args) -> dict:
        controller = self.core.combat_controller
        if controller is None:
            return self._err(ErrorCode.NOT_IMPLEMENTED,
                             "no combat module registered")
        return {"ok": True, **controller("disengage", {})}

    def get_combat_status(self, _args) -> dict:
        controller = self.core.combat_controller
        if controller is None:
            return self._err(ErrorCode.NOT_IMPLEMENTED,
                             "no combat module registered")
        return {"ok": True, **controller("status", {})}
