"""Layer A — World Interface (§3).

Typed access to raw observations and commands. This layer does NOT decide
what is strategically important; it only forwards operations to a pluggable
backend driver (a real Minescript/Pyritone driver, or the simulation driver
used by tests) and lightly normalizes what comes back.

Backend operation vocabulary (implemented by every driver):
    describe, player_state, entity_query, block_query, inventory_count,
    navigate, mine, task_status, cancel_task, chat_command, release_inputs

All coordinates are ints in Minecraft world space. `dimension` uses
'minecraft:overworld' / 'minecraft:nether' / 'minecraft:hell' / 'minecraft:end'.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List

from ..enums import ErrorCode  # only for error payload hints at callers


def clamp_int(value, lo=None, hi=None) -> int:
    v = int(round(float(value)))
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def dist3(a, b) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


class WorldInterface:
    """Typed facade over a raw backend driver (see mcagent.bridge.drivers)."""

    def __init__(self, backend):
        self.backend = backend  # anything with .request(op, args, timeout)

    def describe(self) -> Dict[str, Any]:
        return self.backend.request("describe", {}, timeout=2.0)

    def player_state(self) -> Dict[str, Any]:
        """Player position/orientation, health, hunger, dimension, inventory.

        Returns the raw snapshot; the caller (world model) filters it."""
        snap = self.backend.request("player_state", {}, timeout=5.0)
        if "position" not in snap:
            raise ValueError("player_state snapshot has no position")
        pos = snap["position"]
        snap["position"] = {k: clamp_int(pos[k]) for k in ("x", "y", "z")}
        for key in ("health", "hunger"):
            if snap.get(key) is None:
                snap[key] = None  # explicitly unknown, never invented
        snap.setdefault("alive", True)
        snap.setdefault("dimension", "minecraft:overworld")
        snap.setdefault("environment", {})
        snap.setdefault("inventory", {})
        return snap

    def nearby_entities(self, radius: int) -> Dict[str, Any]:
        r = clamp_int(radius, 1, 128)  # entity queries are always bounded
        out = self.backend.request("entity_query", {"radius": r}, timeout=5.0)
        entities = []
        for e in out.get("entities", []):
            try:
                p = {"x": clamp_int(e["x"]), "y": clamp_int(e["y"]), "z": clamp_int(e["z"])}
            except (KeyError, TypeError, ValueError):
                continue
            d = e.get("distance")
            if d is None:
                d = None  # leave unknown; the model computes it from coords
            entities.append({
                "uuid": str(e.get("uuid", "")),
                "type": str(e.get("type", "unknown")),
                "position": p,
                "distance": d,
                # None when the client does not flag hostility: the entity
                # tracker then falls back to its type table (§ classification).
                "hostile": (bool(e["hostile"]) if "hostile" in e else None),
            })
        return {"radius": r, "entities": entities}

    def query_blocks(self, center, radius: int) -> Dict[str, Any]:
        """Bounded block query. `radius` is clamped; scans are truncated
        (§10: no unbounded scans)."""
        from ..config import current_config

        cfg = current_config()
        r = clamp_int(radius, 1, cfg.tier2_radius)  # any single query is bounded
        # (tier2 is the global cap; deeper 'inspect' areas are capped stricter
        # by the perception layer)
        c = {k: clamp_int(center[k]) for k in ("x", "y", "z")}
        out = self.backend.request("block_query", {"center": c, "radius": r}, timeout=15.0)
        blocks = out.get("blocks", [])
        truncated = bool(out.get("truncated", False))
        if len(blocks) > cfg.max_scan_blocks:
            blocks = blocks[: cfg.max_scan_blocks]
            truncated = True
        return {"center": c, "radius": r, "blocks": blocks, "truncated": truncated}

    def inventory_count(self, item: str) -> int:
        out = self.backend.request("inventory_count", {"item": item}, timeout=5.0)
        try:
            return max(0, int(out.get("count", 0)))
        except (TypeError, ValueError):
            return 0

    def navigate(self, coords, dimension: str, task_id: str) -> Dict[str, Any]:
        args = {
            "x": clamp_int(coords["x"]),
            "y": clamp_int(coords["y"]),
            "z": clamp_int(coords["z"]),
            "dimension": dimension,
            "task_id": task_id,
        }
        return self.backend.request("navigate", args, timeout=5.0)

    def mine(self, block: str, quantity: int, task_id: str, coords=None) -> Dict[str, Any]:
        args = {"block": block, "task_id": task_id}
        if quantity is not None:
            args["quantity"] = max(1, int(quantity))
        if coords:
            args["coords"] = {k: clamp_int(coords[k]) for k in ("x", "y", "z")}
        return self.backend.request("mine", args, timeout=5.0)

    def task_status(self, task_id: str) -> Dict[str, Any]:
        return self.backend.request("task_status", {"task_id": task_id}, timeout=5.0)

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        return self.backend.request("cancel_task", {"task_id": task_id}, timeout=5.0)

    def chat_command(self, text: str, task_id=None) -> Dict[str, Any]:
        """Raw command (Baritone console, when the bridge is chat-based)."""
        args = {"text": str(text)}
        if task_id:
            args["task_id"] = task_id
        return self.backend.request("chat_command", args, timeout=5.0)

    def release_inputs(self) -> Dict[str, Any]:
        """Release held movement inputs (arbiter ownership change, §9)."""
        return self.backend.request("release_inputs", {}, timeout=2.0)

