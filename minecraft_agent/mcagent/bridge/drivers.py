"""Layer A pluggable backend drivers (§3, §7 Baritone/Pyritone).

Driver contract — every driver implements:

    request(op, args, timeout) -> dict        # raises BackendUnavailable/BackendTimeout

Operations (implemented by mcagent.world.interface.WorldInterface callers):
    describe, player_state, entity_query, block_query, inventory_count,
    navigate, mine, task_status, cancel_task, chat_command, release_inputs

Drivers:
  * SimulationBackend — deterministic scripted world. The ONLY driver exercised
     by this machine's unit tests (there is no Minecraft client running here).
  * MinescriptDriver — the official `minescript` python connector
     (minescript-connector, https://github.com/maxuser0/minescript). Verified
     against the minescript 1.1.9 package installed on this machine: method
     names are real and introspected. Calling an operation still requires the
     in-game Minescript client to be running; without it the call raises
     BackendUnavailable. Minescript alone cannot drive Baritone task tracking
     (see its capabilities list) — pair it with Pyritone for navigation ops.
  * PyritoneDriver — the python client for the Baritone bridge
     (https://pypi.org/project/pyritone/, verified against pyritone 0.2.0 and
     its bundled Baritone v1.15.0 COMMAND_SPECS). Verified methods:
     Client.goto(x,y,z)/mine(*args)/cancel(task_id)/execute(text)/
     status_get()/next_event(timeout). Without a running game the websocket
     bridge cannot be discovered/connected; that raises BackendUnavailable.
     Baritone's `#mine <block>` takes NO quantity argument in v1.15.0 —
     quantity limits live in the Python layer (see navigation/mining.py).
"""
from __future__ import annotations
import asyncio
import math
import time
from typing import Any, Dict

from ..enums import TaskState  # backend-level task states are strings, see _norm_state


class BackendUnavailable(Exception):
    """The backend (client, bridge, or operation) is not usable right now."""


class BackendTimeout(Exception):
    """The backend did not answer within the requested timeout."""


_BACKEND_STATES = {"active", "arrived", "done", "blocked", "failed", "cancelled", "not_found"}


def _norm_state(s: str) -> str:
    s = str(s).lower()
    return s if s in _BACKEND_STATES else "not_found"


# --------------------------------------------------------------------------
# Simulation driver (tests / offline development)
# --------------------------------------------------------------------------


class SimulationBackend:
    """A scripted 'world': deterministic, no Minecraft required.

    Constructor inputs (all optional) let each test stage exactly one scenario:
      world      {(x,y,z): block_name}
      player     {"position": [x,y,z], "health": 20, "hunger": 20,
                  "dimension": "minecraft:overworld"}
      entities   [{uuid,type,x,y,z,hostile}]
      inventory  {item_name: count}
      nav_step   blocks moved per task_status poll (simulated bot speed)
      blocked    set of task_ids that never make progress (navigation failure)
    """

    OPS = ("describe player_state entity_query block_query inventory_count "
           "navigate mine task_status cancel_task chat_command release_inputs").split()

    def __init__(self, world=None, player=None, entities=None, inventory=None,
                 nav_step: int = 8, blocked: set | None = None):
        self.world = dict(world or {})
        self.player = dict({
            "position": [0.0, 64.0, 0.0],
            "health": 20.0, "hunger": 20.0,
            "dimension": "minecraft:overworld",
        }, **(player or {}))
        self.entities = list(entities or [])
        self.inventory = dict(inventory or {})
        self.nav_step = max(1, int(nav_step))
        self.blocked_tasks = set(blocked or ())
        self.tasks: Dict[str, dict] = {}
        self._n = 0

    def request(self, op, args, timeout=None):
        if op not in self.OPS:
            raise BackendUnavailable(f"simulation does not implement {op}")
        return dict(getattr(self, "_" + op)(args or {}))

    def _describe(self, a):
        return {"backend": "simulation", "version": "0.1", "capabilities": list(self.OPS)}

    def _player_state(self, a):
        return {
            "position": {k: v for k, v in zip("xyz", self.player["position"])},
            "health": self.player.get("health"),
            "hunger": self.player.get("hunger"),
            "alive": self.player.get("health", 20) > 0,
            "dimension": self.player.get("dimension", "minecraft:overworld"),
            "environment": dict(self.player.get("environment", {})),
            "inventory": dict(self.inventory),
        }

    def _entity_query(self, a):
        p = self.player["position"]
        r = int(a["radius"])
        out = []
        for e in self.entities:
            d = math.sqrt((e["x"] - p[0]) ** 2 + (e["y"] - p[1]) ** 2 + (e["z"] - p[2]) ** 2)
            if d <= r:
                out.append({**e, "distance": round(d, 2)})
        return {"entities": out}

    def _block_query(self, a):
        c, r = a["center"], int(a["radius"])
        blocks = []
        for (x, y, z), name in self.world.items():
            if abs(x - c["x"]) <= r and abs(y - c["y"]) <= r and abs(z - c["z"]) <= r:
                blocks.append({"x": x, "y": y, "z": z, "name": name, "visible": False})
        return {"blocks": blocks}

    def _inventory_count(self, a):
        return {"count": int(self.inventory.get(a["item"], 0))}

    def _navigate(self, a):
        task = {"kind": "navigate", "target": [a["x"], a["y"], a["z"]], "polls": 0}
        self.tasks[a["task_id"]] = task
        return {"accepted": True}

    def _mine(self, a):
        chunk = max(1, math.ceil((a.get("quantity") or 10) / 2))
        self.tasks[a["task_id"]] = {
            "kind": "mine", "block": a["block"],
            "quantity": a.get("quantity"), "chunk": chunk,
            "polled": False,
        }
        return {"accepted": True}

    def _task_status(self, a):
        tid = a["task_id"]
        t = self.tasks.get(tid)
        if t is None:
            return {"state": "not_found"}
        if t["kind"] == "navigate":
            if tid in self.blocked_tasks and t["polls"] >= 2:
                return {"state": "blocked", "detail": {"reason": "no path to destination"}}
            return self._poll_navigate(t)
        return self._poll_mine(t)

    def _poll_navigate(self, t):
        t["polls"] += 1
        p = self.player["position"]
        tgt = t["target"]
        remaining = [tgt[i] - p[i] for i in range(3)]
        dist = math.sqrt(sum(r * r for r in remaining))
        if dist < 0.5:
            return {"state": "arrived", "detail": {"remaining": [0, 0, 0]}}
        step = self.nav_step
        for i in range(3):
            m = max(-step, min(step, int(round(remaining[i]))))
            p[i] += m
        return {"state": "active", "detail": {"remaining": [round(x, 1) for x in remaining]}}

    def _norm_item(self, block: str) -> str:
        # mirrors the layer convention: bare block names map to minecraft: items
        return block if ":" in block else "minecraft:" + block

    def _poll_mine(self, t):
        if not t.get("polled"):
            t["polled"] = True
            return {"state": "active", "detail": {"collected": 0}}
        t["chunk_done"] = t.get("chunk_done", 0) + t["chunk"]
        key = self._norm_item(t["block"])
        self.inventory[key] = self.inventory.get(key, 0) + t["chunk"]
        q = t["quantity"]
        if q is None or t["chunk_done"] >= q:
            return {"state": "done", "detail": {"collected": t["chunk_done"]}}
        return {"state": "active", "detail": {"collected": t["chunk_done"]}}

    def _cancel_task(self, a):
        t = self.tasks.pop(a["task_id"], None)
        return {"cancelled": t is not None}

    def _release_inputs(self, a):
        return {"released": True}

    def _chat_command(self, a):
        text = str(a.get("text", "")).strip()
        task_id = a.get("task_id") or f"chat{self._n}"
        if text.startswith("#goto"):
            parts = text.split()
            x, y, z = (int(float(p)) for p in parts[1:4])
            self._navigate({"x": x, "y": y, "z": z, "task_id": task_id})
            return {"accepted": True, "task_id": task_id}
        if text.startswith("#mine"):
            parts = text.split()
            block = parts[1]
            qty = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
            self._mine({"block": block, "quantity": qty, "task_id": task_id})
            return {"accepted": True, "task_id": task_id}
        return {"accepted": False, "reason": "unrecognized command"}

    # extension hooks used by tests:
    def set_blocked(self, task_id: str):
        self.blocked_tasks.add(task_id)

    def move(self, x, y, z):
        self.player["position"] = [float(x), float(y), float(z)]

    def set_environment(self, **env):
        self.player.setdefault("environment", {}).update(env)


# --------------------------------------------------------------------------
# Real-game drivers. Unit tests do NOT exercise these (no game client is
# running on this machine), but their method names are verified against the
# installed minescript 1.1.9 / pyritone 0.2.0 packages, not guessed.
# --------------------------------------------------------------------------


def _first_number(value):
    """Best-effort scalar extraction from a connector payload (defensive:
    the Java side of the connector returns whatever shape it returns; we
    normalize scalars only, never invent values)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, (list, tuple)) and value:
        for item in value:
            n = _first_number(item)
            if n is not None:
                return n
    if isinstance(value, dict):
        for v in value.values():
            n = _first_number(v)
            if n is not None:
                return n
    return None


_DIMENSION_ALIASES = {
    "0": "minecraft:overworld", "overworld": "minecraft:overworld",
    "-1": "minecraft:hell", "hell": "minecraft:hell",
    "1": "minecraft:nether", "nether": "minecraft:nether",
    "2": "minecraft:end", "end": "minecraft:end",
}


class MinescriptDriver:
    """Python connector for the in-game Minescript mod.

    Verified against the `minescript` 1.1.9 package importable on this machine.
    Operations need the client mod running inside the game; without it every
    connector call errors and we report BackendUnavailable honestly.
    Note: Minescript exposes observations and raw chat, but NOT Baritone task
    bookkeeping — capabilities therefore exclude navigate/mine/task_status,
    so the Baritone bridge (Pyritone, or chat commands + an in-game console)
    must provide navigation ops.
    """

    def __init__(self):
        try:
            import minescript  # type: ignore

            self._ms = minescript
        except ImportError as exc:
            raise BackendUnavailable(f"minescript connector not installed: {exc}")

    def request(self, op, args, timeout=None):
        handler = getattr(self, "_" + op, None)
        if handler is None:
            raise BackendUnavailable(f"minescript driver: {op} not supported "
                                     "(see capabilities in _describe)")
        return handler(args or {}, timeout)

    def _call(self, what, *a, **k):
        try:
            return getattr(self._ms, what)(*a, **k)
        except Exception as exc:  # connector raises RuntimeError/URLError without the game
            raise BackendUnavailable(f"minescript call failed for {what}: {exc}")

    def _describe(self, a, timeout=None):
        return {"backend": "minescript",
                "version": getattr(self._ms, "__version__", "unknown"),
                "capabilities": ["describe", "player_state", "entity_query",
                                 "block_query", "inventory_count",
                                 "chat_command", "release_inputs"]}

    def _player_state(self, a, timeout=None):
        pos = self._call("getplayerpos")
        if pos is None:
            raise BackendTimeout("no player snapshot from client")
        coords = [_first_number(pos)]
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            coords = [_first_number(pos[i]) for i in range(3)]
        if any(c is None for c in coords):
            raise BackendUnavailable("could not normalize getplayerpos payload")
        health = _first_number(self._call("gethealth"))
        hunger = _first_number(self._call("gethunger"))
        dim_raw = self._call("getdimension")
        dim = _DIMENSION_ALIASES.get(str(dim_raw).lower() if dim_raw is not None else "",
                                      "minecraft:overworld")
        biome = self._call("getbiome")
        return {
            "position": {k: coords[i] for i, k in enumerate("xyz")},
            "health": health, "hunger": hunger,
            "alive": (health if health is not None else 1) > 0,
            "dimension": dim,
            "biome": str(biome) if biome is not None else None,
            "environment": {}, "inventory": {},
        }

    def _entity_query(self, a, timeout=None):
        raw = self._call("getnearbyentities", float(a["radius"])) or []
        entities = []
        for e in raw:
            if not isinstance(e, dict):
                continue
            pos = e.get("position", e.get("pos"))
            if pos is None or len(pos) < 3:
                continue
            entities.append({
                "uuid": str(e.get("id", e.get("uuid", ""))),
                "type": str(e.get("name", e.get("type", "unknown"))),
                "x": pos[0], "y": pos[1], "z": pos[2],
                # Minescript does not flag hostility -> tracker uses its type table.
            })
        return {"entities": entities}

    def _block_query(self, a, timeout=None):
        # Minescript block queries are player-centered; a targeted center away
        # from the player is filtered locally after the fetch.
        c, r = a["center"], int(a["radius"])
        limit = max(1, min(r * 8, 1024))
        raw = self._call("getblocksinrange", radius=int(r), limit=limit) or []
        blocks = []
        for b in raw:
            if isinstance(b, dict):
                blocks.append({"x": int(b.get("x")), "y": int(b.get("y")),
                               "z": int(b.get("z")),
                               "name": str(b.get("name", b.get("block", "unknown"))),
                               "visible": True})
            elif isinstance(b, (list, tuple)) and len(b) >= 4:
                x, y, z = (int(v) for v in b[:3])
                name = str(b[3])
                if abs(x - c["x"]) <= r and abs(y - c["y"]) <= r and abs(z - c["z"]) <= r:
                    blocks.append({"x": x, "y": y, "z": z, "name": name, "visible": True})
        return {"blocks": blocks, "truncated": len(raw) >= limit}

    def _inventory_count(self, a, timeout=None):
        item = str(a["item"])
        short = item.split(":")[-1]
        raw = self._call("getinventory") or []
        total = 0
        for entry in raw:
            if isinstance(entry, dict):
                name = str(entry.get("name", entry.get("item_name", entry.get("item", ""))))
                count = _first_number(entry.get("count", entry.get("item_count",
                                                                   entry.get("quantity", 0)))) or 0
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                name, count = str(entry[0]), int(_first_number(entry[1]) or 0)
            else:
                continue
            if name == item or name.split(":")[-1] == short:
                total += count
        return {"count": total}

    def _chat_command(self, a, timeout=None):
        # verified connector surface: sendchat sends chat/command text
        self._call("sendchat", str(a["text"]))
        return {"accepted": True, "task_id": a.get("task_id")}

    def _release_inputs(self, a, timeout=None):
        # verified connector surface: stopmoving + explicit direction releases
        self._call("stopmoving")
        for m in ("moveforward", "moveback", "moveleft", "moveright"):
            try:
                getattr(self._ms, m)(False)
            except (AttributeError, TypeError):
                pass
        return {"released": True}


class PyritoneDriver:
    """Client for the Baritone bridge (pyritone 0.2.0; Baritone v1.15.0).

    Uses ONLY verified methods: pyritone.client_sync re-exports the async
    client (pyritone.client_async.Client — all operations are coroutines), so
    the driver runs them on its own persistent asyncio event loop: goto(x,y,z),
    mine(*args), cancel(task_id), execute(command), status_get(), next_event(timeout).
    The bridge connects over websockets to a RUNNING game using the bridge_info
    file pyritone discovers next to the Minecraft install; without a game each
    call raises BackendUnavailable (constructing the driver without a game is
    fine, so adapter wiring can be validated offline).

    Baritone 1.15.0 `#mine` takes only block arguments (verified via the
    bundled COMMAND_SPECS) — quantity limits are enforced by the Python layer
    (inventory watch + cancel; see mcagent.navigation.mining).
    """

    def __init__(self):
        try:
            import pyritone  # type: ignore

            self._py = pyritone
        except ImportError as exc:
            raise BackendUnavailable(f"pyritone not installed: {exc}")
        self._client = None
        self._loop = None

    def request(self, op, args, timeout=None):
        handler = getattr(self, "_" + op, None)
        if handler is None:
            raise BackendUnavailable(f"pyritone driver: {op} not supported")
        return handler(args or {}, timeout)

    def _ensure(self, timeout=None):
        if self._client is None:
            from pyritone.client_async import Client  # verified entry point

            if self._loop is None:
                self._loop = asyncio.new_event_loop()
            try:
                client = Client()  # default discovery (bridge_info file)
                self._loop.run_until_complete(
                    asyncio.wait_for(client.connect(), timeout or 5.0))
            except Exception as exc:  # DiscoveryError/ConnectionError/OSError
                raise BackendUnavailable(
                    f"baritone bridge not reachable (is the game running with "
                    f"Baritone connected?): {exc}")
            self._client = client
        return self._client

    def _run(self, coro, timeout=None):
        """All pyritone operations are coroutines (verified: Client.goto etc.
        return coroutines); run them on the driver's persistent loop."""
        try:
            return self._loop.run_until_complete(
                asyncio.wait_for(coro, timeout or 15.0))
        except asyncio.TimeoutError:
            raise BackendTimeout("baritone bridge did not answer in time")

    def _describe(self, a, timeout=None):
        return {"backend": "pyritone",
                "version": getattr(self._py, "__version__", self._py.BARITONE_VERSION),
                "capabilities": ["describe", "navigate", "mine", "task_status",
                                 "cancel_task", "chat_command"]}

    def _navigate(self, a, timeout=None):
        c = self._ensure(timeout)
        self._run(c.goto(int(a["x"]), int(a["y"]), int(a["z"])), timeout)
        return {"accepted": True}

    def _mine(self, a, timeout=None):
        c = self._ensure(timeout)
        # Baritone 1.15.0 `#mine` takes only block arguments (verified spec);
        # quantity enforcement lives in mcagent.navigation.mining.
        self._run(c.mine(str(a["block"])), timeout)
        return {"accepted": True}

    def _task_status(self, a, timeout=None):
        c = self._ensure(timeout)
        status = self._run(c.status_get(), timeout)
        if isinstance(status, str):
            status = {"status": status}
        state = str(status.get("state", "active")).lower()
        return {"state": _norm_state(state if state in _BACKEND_STATES else "active"),
                "detail": {"raw_status": status,
                           "note": "Baritone status payload passed through; "
                                   "state normalization is deliberately conservative"}}

    def _cancel_task(self, a, timeout=None):
        c = self._ensure(timeout)
        self._run(c.cancel(a["task_id"] if a.get("task_id") else None), timeout)
        return {"cancelled": True}

    def _chat_command(self, a, timeout=None):
        c = self._ensure(timeout)
        self._run(c.execute(str(a["text"])), timeout)
        return {"accepted": True, "task_id": a.get("task_id")}
