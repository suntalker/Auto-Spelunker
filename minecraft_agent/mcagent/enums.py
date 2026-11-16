"""Shared enumerations for the world-awareness / navigation subsystem.

Kept in one leaf module so that world/, navigation/ and agent/ layers can
import it without circular dependencies.
"""
from __future__ import annotations
from enum import Enum


class KnowledgeState(str, Enum):
    """How the world model qualifies a piece of information (§3 Layer B)."""

    OBSERVED = "OBSERVED"   # from a recent, live observation
    KNOWN = "KNOWN"        # previously discovered and stored in world memory
    INFERRED = "INFERRED"  # estimated from other observations
    UNKNOWN = "UNKNOWN"    # not established


class NavState(str, Enum):
    """Navigation/mining task states (§7)."""

    IDLE = "IDLE"
    PLANNING = "PLANNING"
    NAVIGATING = "NAVIGATING"
    MINING = "MINING"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    PAUSED = "PAUSED"  # interrupted by another movement owner


class MiningMode(str, Enum):
    """Dual-mode mining strategies (§5)."""

    EXPOSED = "EXPOSED"
    TUNNEL = "TUNNEL"
    AUTO = "AUTO"


class MovementOwner(str, Enum):
    """Centralized movement ownership (§9)."""

    NAVIGATION = "NAVIGATION"
    COMBAT = "COMBAT"
    MANUAL = "MANUAL"
    EMERGENCY = "EMERGENCY"


class TaskState(str, Enum):
    """Lifecycle states for any long-running operation (§7 adapter side)."""

    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    PAUSED = "paused"  # interrupted by another movement owner, resumable


class ErrorCode(str, Enum):
    """Structured error codes returned to the LLM (§9 adapter error handling)."""

    NOT_CONNECTED = "MC_NOT_CONNECTED"
    PLAYER_UNAVAILABLE = "MC_PLAYER_UNAVAILABLE"
    LOCATION_NOT_FOUND = "LOCATION_NOT_FOUND"
    INVALID_DIMENSION = "INVALID_DIMENSION"
    INVALID_ARGS = "INVALID_ARGS"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    NAVIGATION_BRIDGE_UNAVAILABLE = "NAVIGATION_BRIDGE_UNAVAILABLE"
    MOVEMENT_BUSY = "MOVEMENT_BUSY"        # another controller owns movement
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    OPERATION_TIMED_OUT = "OPERATION_TIMED_OUT"
    DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL = "INTERNAL"


# Event names from §8 (kept as plain strings in payloads; enum for typos).
EVENT_NAMES = {
    "hostile_detected",
    "hostile_lost",
    "resource_discovered",
    "location_reached",
    "navigation_failed",
    "health_critical",
    "hunger_critical",
    "new_structure_discovered",
    "environmental_hazard_detected",
    "ownership_changed",
    "connection_lost",
    "location_saved",
}
