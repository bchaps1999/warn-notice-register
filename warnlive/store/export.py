"""Export consolidated + per-state CSVs from SQLite (never from raw files)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from warnlive.normalize.engine import normalized_employer

EXPORT_COLUMNS = [
    "state",
    "employer_name",
    "location",
    "notice_date",
    "effective_date",
    "employees_affected",
    "layoff_type",
    "is_temporary",
    "is_amendment",
    "is_amended",
    "current_version",
    "source_url",
    "source_notice_id",
    "dedupe_key",
    "first_seen",
    "last_seen",
]


def export_csvs(
    conn: sqlite3.Connection,
    export_dir: Path,
    active_states: list[str],
) -> dict[str, int]:
    """Write warn_notices.csv (active states only) and per-state CSVs.

    Rows are stably sorted so successive exports diff cleanly in git.
    Returns row counts per file written.
    """
    export_dir = Path(export_dir)
    (export_dir / "states").mkdir(parents=True, exist_ok=True)
    active_upper = sorted(s.upper() for s in active_states)
    counts: dict[str, int] = {}

    def fetch(where: str, params: tuple) -> list[sqlite3.Row]:
        return conn.execute(
            f"SELECT {', '.join(EXPORT_COLUMNS)} FROM notices WHERE {where} "
            "ORDER BY state, notice_date, employer_name, dedupe_key",
            params,
        ).fetchall()

    # normalized_name is derived at export time (cleanco suffix stripping),
    # inserted right after employer_name; the DB keeps only source values.
    header = EXPORT_COLUMNS[:2] + ["normalized_name"] + EXPORT_COLUMNS[2:]

    def write(path: Path, rows: list[sqlite3.Row]) -> None:
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(
                [(r[0], r[1], normalized_employer(r[1]), *tuple(r)[2:]) for r in rows]
            )
        counts[str(path)] = len(rows)

    if active_upper:
        placeholders = ",".join("?" * len(active_upper))
        rows = fetch(f"state IN ({placeholders})", tuple(active_upper))
    else:
        rows = []
    write(export_dir / "warn_notices.csv", rows)

    for state in active_upper:
        write(
            export_dir / "states" / f"{state.lower()}.csv",
            fetch("state = ?", (state,)),
        )
    return counts
