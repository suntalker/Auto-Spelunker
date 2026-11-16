"""Shared test support: per-test isolated SQLite db + seeded adapter factory."""
import os
import tempfile

from mcagent.config import configure
from mcagent.adapter import MinecraftAgentAdapter


def isolate_db():
    """Point persistence at a fresh temp db so tests never share state."""
    d = tempfile.mkdtemp(prefix="mcagent_test_")
    configure(db_path=os.path.join(d, "world.db"))
    return d


def fresh_adapter(seed=True):
    """Adapter over a deterministic simulation world (a small village + ore)."""
    a = MinecraftAgentAdapter()
    if seed:
        a.backend.world.update({
            (40, 64, 0): "iron_ore",
            (41, 64, 0): "iron_ore",
            (40, 63, 0): "iron_ore",
            # buried ore: fully enclosed on all 6 orthogonal sides
            (45, 64, 0): "iron_ore",
            (45, 65, 0): "minecraft:bedrock",
            (45, 63, 0): "minecraft:bedrock",
            (44, 64, 0): "minecraft:bedrock",
            (46, 64, 0): "minecraft:bedrock",
            (45, 64, 1): "minecraft:bedrock",
            (45, 64, -1): "minecraft:bedrock",
            (100, 64, 100): "minecraft:planks",  # structure marker sample
        })
        a.backend.entities.append(
            {"uuid": "z1", "type": "minecraft:zombie", "x": 3, "y": 64, "z": 0,
             "hostile": True})
        a.backend.inventory.setdefault("minecraft:iron_ore", 0)
    return a
