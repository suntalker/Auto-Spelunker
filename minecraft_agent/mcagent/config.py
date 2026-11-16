"""Persistent configuration (§ suggested project structure: config/world_config.py).

Values are conservative defaults for a continuously-running agent. Spatial
limits are enforced here so no scan can quietly become unbounded (§10).

Biome names: static reference list, fetched once from the official "All Biomes
in Minecraft" help article (help.minecraft.net article 360046470431, fetched
2026-01). Biome values are still never validated against it (§ "current biome
if available" — perception passes through whatever the client reports); this
list is informational metadata only, used for display and hazard hints.
Re-fetch that article if the game renames biomes again.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[1]  # .../minecraft_agent


@dataclass(frozen=True)
class WorldConfig:
    # --- persistence ---------------------------------------------------------
    db_path: str = str(PROJECT_ROOT / "data" / "world.db")

    # --- observation pacing (§10: separate observation vs LLM frequency) -----
    passive_interval_s: float = 2.0        # minimum seconds between passive Tier-1 sweeps
    observe_max_age_s: float = 10.0       # younger than this => "OBSERVED"
    known_stale_revalidate_s: float = 900.0  # revalidate deposits older than this
    entity_radius: int = 32               # Tier-1 entity query radius

    # --- perception tiers (§4) ------------------------------------------------
    tier1_radius: int = 48                # passive awareness radius (cheap ops only)
    tier2_radius: int = 96                # max contextual (objective-driven) scan
    tier3_max_radius: int = 32            # hard cap for deep inspection scans
    default_scan_radius: int = 24
    max_scan_blocks: int = 4096           # a scan is truncated beyond this
    cell_size: int = 16                   # explored-region grid cell (1 chunk)
    rescan_cooldown_s: float = 300.0      # skip recently-scanned cells unless forced

    # --- survival thresholds --------------------------------------------------
    health_critical: float = 6.0
    hunger_critical: float = 6.0

    # --- events (§8 throttling/dedup) -----------------------------------------
    event_throttle_s: float = 30.0
    max_pending_events: int = 64

    # --- navigation (§7) ------------------------------------------------------
    nav_timeout_s: float = 600.0
    stuck_polls: int = 3                  # polls without progress => BLOCKED
    default_poll_interval_s: float = 2.0

    # --- mining (§5) -----------------------------------------------------------
    default_mining_mode: str = "AUTO"     # EXPOSED | TUNNEL | AUTO
    mine_max_radius: int = 96
    baritone_goto_template: str = "#goto {x} {y} {z}"
    baritone_mine_template: str = "#mine {block}"        # Baritone 1.15.0 #mine takes only a block (verified via pyritone COMMAND_SPECS); quantity is enforced by the Python layer
    # Baritone's #mine syntax and its quantity-limit behaviour were NOT
    # verifiable offline (see README "Unverified APIs"). Quantity limits are
    # enforced at the Python orchestration layer regardless.

    # --- movement ownership ----------------------------------------------------
    emergency_priority: bool = True


def current_config() -> WorldConfig:
    return _CURRENT


_CURRENT: WorldConfig = WorldConfig()


def configure(**overrides) -> WorldConfig:
    """Return a new immutable config with overrides applied (tests/dev tooling)."""
    global _CURRENT
    _CURRENT = replace(_CURRENT, **overrides)
    return _CURRENT


def reset(**overrides) -> WorldConfig:
    """Return a throwaway config (tests use this; does not mutate global)."""
    return replace(WorldConfig(), **overrides)


# Display names from the official help article (source: help.minecraft.net
# article 360046470431, fetched 2026-01). Internal snake_case ids are derived
# by lower/underscore; the client may report either form, so both are kept.
BIOME_GROUPS = {
    "overworld": [
        # offshore
        "ocean", "deep_ocean", "warm_ocean", "lukewarm_ocean", "deep_lukewarm_ocean",
        "cold_ocean", "deep_cold_ocean", "frozen_ocean", "deep_frozen_ocean",
        "mushroom_fields",
        # highland
        "jagged_peaks", "frozen_peaks", "stony_peaks", "meadow", "cherry_grove",
        "grove", "snowy_slopes", "windswept_hills", "windswept_gravelly_hills",
        "windswept_forest",
        # woodland
        "forest", "flower_forest", "taiga", "old_growth_pine_taiga", "old_growth_spruce_taiga",
        "snowy_taiga", "birch_forest", "old_growth_birch_forest", "dark_forest",
        "pale_garden", "jungle", "sparse_jungle", "bamboo_jungle",
        # wetland
        "river", "frozen_river", "swamp", "mangrove_swamp", "beach", "snowy_beach",
        "stony_shore",
        # flatland
        "plains", "sunflower_plains", "snowy_plains", "ice_spikes",
        # arid-land
        "desert", "savanna", "savanna_plateau", "windswept_savanna", "badlands",
        "wooded_badlands", "eroded_badlands",
        # cave biomes (underground hazard hints)
        "deep_dark", "dripstone_caves", "lush_caves", "sulfur_caves",
        # java only
        "the_void",
    ],
    "nether": ["nether_wastes", "soul_sand_valley", "crimson_forest", "warped_forest", "basalt_deltas"],
    "end": ["the_end", "end_barrens", "end_highlands", "end_midlands", "small_end_islands"],
}

BIOMES_REFERENCE: frozenset = frozenset(
    name for group in BIOME_GROUPS.values() for name in group
)
