"""SQLite persistence for the expanding world model (§6 + §5 registries).

Tables:
  locations          named-coordinate registry (home, village_1, ...)
  resource_deposits  separate registry for discovered resource blocks (§5.6)
  explored           coarse grid cells already scanned (avoids blind rescans)
  nav_history       task transitions (recorded for later inspection/logging)
  settings           persistent agent settings (e.g. mining mode §5.4)

Uses only the stdlib sqlite3. State survives a Python-process restart by
design (see tests/test_location_registry.py::test_survives_process_restart).
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

from ..enums import TaskState  # noqa: F401 (used by callers, kept for schema docs)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    name TEXT PRIMARY KEY,
    x INTEGER, y INTEGER, z INTEGER,
    dimension TEXT NOT NULL,
    category TEXT DEFAULT 'other',
    description TEXT DEFAULT '',
    protected INTEGER DEFAULT 0,
    verified INTEGER DEFAULT 0,
    discovered_at REAL DEFAULT 0,
    last_verified REAL DEFAULT 0,
    updated_at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS resource_deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    block TEXT NOT NULL,
    x INTEGER, y INTEGER, z INTEGER,
    dimension TEXT DEFAULT 'minecraft:overworld',
    exposure TEXT DEFAULT 'unknown',
    quantity_estimate INTEGER DEFAULT 0,
    discovered_at REAL DEFAULT 0,
    last_verified REAL DEFAULT 0,
    status TEXT DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS explored (
    dimension TEXT, cell_x INTEGER, cell_z INTEGER,
    block TEXT, scanned_at REAL DEFAULT 0, radius INTEGER DEFAULT 0,
    PRIMARY KEY (dimension, cell_x, cell_z, block)
);
CREATE TABLE IF NOT EXISTS nav_history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT, kind TEXT, destination TEXT, state TEXT, reason TEXT DEFAULT '', at REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""

_KNOWN_DIMENSIONS = {"minecraft:overworld", "minecraft:nether", "minecraft:hell", "minecraft:end"}


def valid_dimension(dim: str) -> bool:
    """Validate dimensions (§6). The canonical dimensions are exact matches;
    other ``minecraft:`` names are accepted as unknown-custom (callers may
    warn), everything else is rejected."""
    if not isinstance(dim, str) or not dim:
        return False
    if dim in _KNOWN_DIMENSIONS:
        return True
    return dim.startswith("minecraft:") and len(dim) > len("minecraft:")


def open_db(path: str | None = None):
    """Open (creating parents if needed) the world database and ensure schema."""
    from ..config import current_config

    p = Path(path or current_config().db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p))
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


class WorldDatabase:
    """Thin typed accessor over the schema above. No business logic."""

    def __init__(self, conn):
        self.conn = conn

    # ---- settings (§5.4 persistent mining mode) ----
    def settings_get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row is not None else default

    def settings_set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO settings VALUES (:key, :value)",
            {"key": key, "value": str(value)},
        )
        self.conn.commit()

    def settings_delete(self, key: str) -> None:
        self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self.conn.commit()

    # ---- task history (§7/§10 logging) ----
    def log_task(self, task_id: str, kind: str, destination: str, state, reason: str = "", at: float = 0.0):
        if hasattr(state, "value"):
            state = state.value
        self.conn.execute(
            "INSERT INTO nav_history VALUES (NULL, :task_id, :kind, :destination, :state, :reason, :at)",
            {"task_id": task_id, "kind": kind, "destination": destination,
             "state": str(state), "reason": reason, "at": at},
        )
        self.conn.commit()

    def task_history(self, task_id: str):
        rows = self.conn.execute(
            "SELECT seq, state, reason, at FROM nav_history WHERE task_id=?", (task_id,)
        ).fetchall()
        return [{"seq": r[0], "state": r[1], "reason": r[2], "at": r[3]} for r in rows]

    def commit(self):
        self.conn.commit()
