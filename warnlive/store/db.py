"""SQLite connection and schema management."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"
SCHEMA_VERSION = 4  # v4: site_address enrichment column

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
    _drop_last_seen_not_null(conn)
    _add_site_address(conn)
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
    elif row["version"] < SCHEMA_VERSION:
        # Additive DDL is handled by re-running schema.sql above; changes
        # to an existing table get an explicit migration (see v3 above).
        conn.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION,))
    elif row["version"] > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {row['version']} is newer than code "
            f"version {SCHEMA_VERSION}; refusing to write."
        )
    conn.commit()


def _add_site_address(conn: sqlite3.Connection) -> None:
    """v4: additive column; detected from the live table like v3."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(notices)")}
    if cols and "site_address" not in cols:
        conn.execute("ALTER TABLE notices ADD COLUMN site_address TEXT")


def _drop_last_seen_not_null(conn: sqlite3.Connection) -> None:
    """v3: rebuild notices so last_seen may be NULL.

    Detected from the live table rather than the stamped version, because
    the old strategy stamped versions without altering existing tables.
    SQLite cannot drop a NOT NULL in place, so this is the standard
    rebuild: copy, drop, rename — with foreign keys off for the duration,
    since notice_versions and notice_links reference notices(id) and the
    ids are preserved exactly.
    """
    info = {r["name"]: r for r in conn.execute("PRAGMA table_info(notices)")}
    if not info or not info["last_seen"]["notnull"]:
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            CREATE TABLE notices_v3 (
                id INTEGER PRIMARY KEY,
                dedupe_key TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                employer_name TEXT,
                location TEXT,
                notice_date TEXT,
                effective_date TEXT,
                employees_affected INTEGER,
                layoff_type TEXT,
                is_temporary INTEGER,
                is_amendment INTEGER DEFAULT 0,
                source_url TEXT,
                source_notice_id TEXT,
                is_amended INTEGER DEFAULT 0,
                current_version INTEGER DEFAULT 1,
                first_seen TEXT NOT NULL,
                last_seen TEXT
            );
            INSERT INTO notices_v3 SELECT * FROM notices;
            DROP TABLE notices;
            ALTER TABLE notices_v3 RENAME TO notices;
            CREATE INDEX IF NOT EXISTS idx_notices_state_date
                ON notices(state, notice_date);
            """
        )
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(
                f"last_seen migration broke {len(bad)} foreign key(s); rolled back"
            )
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
