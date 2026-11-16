"""Composition layer: subsystems wired into one adapter (§6).

The adapter owns: backend interface (A), world model + registries (B),
Baritone-backed task managers, the central arbiter, and the tool surface
(C). It exposes exactly the capabilities of the active backend: with the
simulation backend every tool works against a deterministic world; with a
Minescript/Pyritone backend the same tools map onto their verified
operations, and anything unverified is reported, not simulated.
"""
from __future__ import annotations
import json

from .enums import ErrorCode
from .bridge.drivers import BackendUnavailable, BackendTimeout


class MinecraftAgentAdapter:
    def __init__(self, mode: str = "simulation"):
        from .bridge.drivers import SimulationBackend, MinescriptDriver, PyritoneDriver
        from .world.interface import WorldInterface
        from .world.database import WorldDatabase, open_db
        from .world.events import EventQueue
        from .world.entity_tracker import EntityTracker
        from .world.world_model import WorldModel
        from .world.location_registry import LocationRegistry
        from .world.resource_scanner import ResourceScanner
        from .world.perception import Perception
        from .navigation.tasks import TaskTracker
        from .navigation.baritone import BaritoneBridge
        from .navigation.manager import NavigationManager
        from .navigation.mining import MiningCoordinator
        from .agent.action_arbiter import ActionArbiter
        from .agent.tools import WorldTools

        if mode == "simulation":
            backend = SimulationBackend()
        elif mode == "minescript":
            backend = MinescriptDriver()
        elif mode == "pyritone":
            backend = PyritoneDriver()
        else:
            raise ValueError(f"unknown backend mode {mode!r}")

        self.backend = backend
        self.iface = WorldInterface(backend)

        # shared state (construction order matters: deps first)
        self.db = WorldDatabase(open_db())
        self.events = EventQueue()
        self.tracker = EntityTracker(self.events)
        self.model = WorldModel(self.iface, self.db, self.events, self.tracker)
        self.registry = LocationRegistry(self.db, self.events)
        self.scanner = ResourceScanner(self.iface, self.db, self.events, self.model)
        self.perception = Perception(self.model, self.scanner)
        self.tasks = TaskTracker(self.db)
        self.bridge = BaritoneBridge(self.iface, self.tasks, self.events,
                                     self.iface.describe())
        self.nav = NavigationManager(self.bridge, self.model, self.registry,
                                     self.tasks, self.events, self.db, None)
        self.mining = MiningCoordinator(self.bridge, self.scanner, self.model,
                                        self.tasks, self.events, self.db)
        self.arbiter = ActionArbiter(self.iface, self.events, self.model)
        self.nav.arbiter = self.arbiter

        self.tools = WorldTools(self)
        self.combat_controller = None  # separate module; hook via register_combat_controller

    def register_combat_controller(self, controller):
        """Hook for the separate combat module (§9/§11: tools exist only
        when a working implementation registers)."""
        self.combat_controller = controller

    def list_tools(self):
        return sorted(self.tools.schemas().keys())

    def call(self, name: str, args: dict | None = None):
        """Single entry point: validation + structured result envelope."""
        if name not in self.tools.schemas():
            return {"success": False,
                    "error": {"code": ErrorCode.UNKNOWN_TOOL.value
                              if hasattr(ErrorCode, "UNKNOWN_TOOL") else "UNKNOWN_TOOL",
                              "message": f"no such tool {name!r}"}}
        out = self.tools._validate(name, args or {})
        if out:
            return {"success": False, "error": out["error"]}
        handler = getattr(self.tools, name, None)
        if handler is None:
            return {"success": False,
                    "error": {"code": ErrorCode.NOT_IMPLEMENTED.value,
                              "message": "tool has no implementation"}}
        try:
            out = handler(args or {})
        except (BackendUnavailable, BackendTimeout) as exc:
            return {"success": False,
                    "error": {"code": "BACKEND_UNAVAILABLE",
                              "message": f"the game bridge said no: {exc}"}}
        except Exception as exc:
            return {"success": False,
                    "error": {"code": "INTERNAL",
                              "message": f"{type(exc).__name__}: {exc}"}}
        if out.get("ok"):
            result = {k: v for k, v in out.items() if k not in ("ok", "error")}
            return {"success": True, "result": result}
        error = out.get("error") or {"code": ErrorCode.INTERNAL.value,
                                     "message": "handler failed"}
        details = {k: v for k, v in out.items() if k not in ("ok", "error")}
        return {"success": False, "error": error, **details}

    def to_json(self, envelope) -> str:
        return json.dumps(envelope, default=str)
