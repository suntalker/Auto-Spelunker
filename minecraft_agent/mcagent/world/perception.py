"""§4 Tiered perception orchestration.

Tier 1 (passive) lives in WorldModel.passive_observe — cheap only.
Tier 2 (contextual) dispatches observations to the CURRENT objective.
Tier 3 (deep inspection) is a bounded targeted scan through ResourceScanner/
WorldInterface (radius clamped everywhere; no indiscriminate 12-chunk scans).
"""
from __future__ import annotations
from typing import Optional, List

from ..config import current_config
from ..enums import KnowledgeState
from .world_model import WorldModel
from .resource_scanner import ResourceScanner
from .entity_tracker import EntityTracker  # noqa: F401 (type source for callers)

# structure/landmark detection block markers (villages, ruins...) —
# client-reported names are matched by bare suffix too.
STRUCTURE_MARKERS = {
    "door", "window", "planks", "wooden_door", "brick", "chimney",
    "village", "house", "lantern", "campfire", "brazier", "well",
    "statue", "mansion", "igloo", "trail_ruins", "pyramid",
}
RESOURCE_SUFFIXES = {
    "iron_ore", "copper_ore", "gold_ore", "coal", "diamond", "emerald",
    "obsidian", "sulfur", "cinnabar", "coals_ore", "iron", "copper",
}
HAZARD_BLOCKS = {"lava", "fire", "magma", "deep_water", "spiked", "spikes"}


class Perception:
    def __init__(self, model: WorldModel, scanner: ResourceScanner):
        self.model = model
        self.scanner = scanner

    # ---------------- Tier 1 ----------------

    def passive_observe(self, force: bool = False) -> bool:
        return self.model.passive_observe(force=force)

    # ---------------- Tier 2: objective-driven ----------------

    def objective_find_resource(self, block: str, radius: Optional[int] = None,
                               allow_rescan: bool = False) -> List[dict]:
        cfg = current_config()
        radius = min(radius or cfg.tier2_radius, cfg.tier2_radius)
        px, py, pz = self.model.player_position()
        return self.scanner.scan(block, {"x": px, "y": py, "z": pz}, radius,
                                 allow_rescan=allow_rescan)

    def objective_find_structure(self, radius: Optional[int] = None) -> List[dict]:
        """Scan for structure landmark blocks (village/ruins hunters)."""
        cfg = current_config()
        radius = min(radius or 32, cfg.tier3_max_radius)
        px, py, pz = self.model.player_position()
        res = self.model.iface.query_blocks({"x": px, "y": py, "z": pz}, radius)
        found = [b for b in res["blocks"]
                 if b["name"].split(":")[-1] in STRUCTURE_MARKERS]
        now = self.model.now()
        self.model.note_blocks(found)
        if found:
            first = found[0]
            self.model.events.emit(
                "new_structure_discovered",
                {"block": first["name"], "coordinates": {k: first[k] for k in "xyz"},
                 "count": len(found)},
                dedup_key="structure:any", now=now,
            )
        return found

    # ---------------- Tier 3: deep inspection ----------------

    def inspect_area(self, center, radius: int) -> dict:
        """Bounded deep scan; returns observations qualified by knowledge
        state. Nothing here is presented as current unless OBSERVED."""
        res = self.model.iface.query_blocks(center, radius)
        self.model.note_blocks(res["blocks"])
        relevant, other = [], 0
        for b in res["blocks"]:
            name_short = b["name"].split(":")[-1]
            if name_short in RESOURCE_SUFFIXES or name_short in STRUCTURE_MARKERS or name_short in HAZARD_BLOCKS:
                rel = "resource" if name_short in RESOURCE_SUFFIXES else \
                      "structure" if name_short in STRUCTURE_MARKERS else "hazard"
                relevant.append({"name": b["name"], "x": b["x"], "y": b["y"],
                                 "z": b["z"], "relevance": rel,
                                 "knowledge": KnowledgeState.OBSERVED.value})
            else:
                other += 1
        return {
            "center": center,
            "radius": res["radius"],
            "relevant": relevant,
            "other_block_count": other,
            "truncated": res["truncated"],
            "observed_at": self.model.now(),
        }
