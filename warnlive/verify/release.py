"""Fail closed when published exports and site data disagree with the database."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def _rows(path: Path):
    with path.open(newline="") as handle:
        yield from csv.DictReader(handle)


def verify(db_path: Path, exports: Path, site_data: Path | None = None) -> dict:
    db_path, exports = Path(db_path), Path(exports)
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("database foreign key check failed")
        notices = {
            row["dedupe_key"]: (row["state"], row["notice_date"] or "",
                                row["effective_date"] or "",
                                row["employees_affected"])
            for row in conn.execute(
                "SELECT dedupe_key,state,notice_date,effective_date,employees_affected FROM notices")
        }
        if len(notices) != conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]:
            raise ValueError("database has duplicate notice keys")
        db_observations = conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0]
    finally:
        conn.close()

    seen: set[str] = set()
    per_state: dict[str, set[str]] = defaultdict(set)
    workers = 0
    for row in _rows(exports / "warn_notices.csv"):
        key = row["dedupe_key"]
        if key in seen or key not in notices:
            raise ValueError(f"duplicate or unknown exported notice: {key}")
        state, notice_day, action_day, count = notices[key]
        if (row["state"], row["notice_date"], row["effective_date"],
                row["employees_affected"]) != (
                    state, notice_day, action_day, "" if count is None else str(count)):
            raise ValueError(f"exported notice disagrees with database: {key}")
        seen.add(key)
        per_state[state].add(key)
        workers += count or 0
    if seen != notices.keys():
        raise ValueError(f"national export has {len(seen)} of {len(notices)} database notices")

    for state, keys in per_state.items():
        file_rows = list(_rows(exports / "states" / f"{state.lower()}.csv"))
        file_keys = [row["dedupe_key"] for row in file_rows]
        if len(file_keys) != len(keys) or set(file_keys) != keys:
            raise ValueError(f"{state} export does not match national export")
    for path in (exports / "states").glob("*.csv"):
        if path.stem.upper() not in per_state and any(_rows(path)):
            raise ValueError(f"unexpected nonempty state export: {path}")

    exported_observations = sum(1 for _ in _rows(exports / "source_observations.csv"))
    if exported_observations != db_observations:
        raise ValueError("source observation export does not match database")

    if site_data is not None:
        meta = json.loads((Path(site_data) / "meta.json").read_text())
        totals = meta["totals"]
        if totals["notices"] != len(notices) or totals["workers"] != workers:
            raise ValueError("site totals do not match database and exports")
        for state, keys in per_state.items():
            if meta["states"][state]["notices"] != len(keys):
                raise ValueError(f"site {state} count does not match export")

    return {"notices": len(notices), "workers": workers,
            "states": len(per_state), "source_observations": db_observations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/warn.sqlite"))
    parser.add_argument("--exports", type=Path, default=Path("data/exports"))
    parser.add_argument("--site-data", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.db, args.exports, args.site_data), sort_keys=True))
