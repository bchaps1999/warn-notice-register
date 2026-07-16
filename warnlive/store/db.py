"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 1

DEFAULT_DB_PATH = Path("data/warn.sqlite")


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] != SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {row['version']} != code version {SCHEMA_VERSION}; "
            "write a migration before proceeding."
        )
    conn.commit()
