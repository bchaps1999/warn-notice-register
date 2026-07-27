"""Export consolidated + per-state CSVs from SQLite (never from raw files)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

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
            f"SELECT {', '.join(EXPORT_COLUMNS)}, "
            "(SELECT v.fields_json FROM notice_versions v "
            " WHERE v.notice_id = notices.id AND v.version = notices.current_version"
            ") AS fields_json "
            f"FROM notices WHERE {where} "
            "ORDER BY state, notice_date, employer_name, dedupe_key",
            params,
        ).fetchall()

    # Derived columns, inserted right after employer_name; the DB keeps only
    # source values. They come from the reference files under
    # data/reference and are empty until those are built (warnlive
    # edgar-refresh, edgar-sic-refresh, nonprofit-refresh, gleif-refresh,
    # wikidata-refresh); see warnlive.enrich.annotate.
    from warnlive.enrich.annotate import FIELDS as IDENTITY_COLUMNS, Annotator
    from warnlive.enrich.places import RESULT_FIELDS as PLACE_COLUMNS, Resolver

    annotator = Annotator()
    annotator.prime(conn)
    # Geography belongs to the notice rather than the employer, so it is
    # resolved separately and merged in beside the identity columns.
    resolver = Resolver()
    DERIVED_COLUMNS = IDENTITY_COLUMNS + PLACE_COLUMNS
    header = EXPORT_COLUMNS[:2] + DERIVED_COLUMNS + EXPORT_COLUMNS[2:]
    date_idx = EXPORT_COLUMNS.index("notice_date")
    eff_idx = EXPORT_COLUMNS.index("effective_date")
    loc_idx = EXPORT_COLUMNS.index("location")

    def derived(r: sqlite3.Row) -> tuple:
        extra = annotator.annotate(
            r[1], r[date_idx] or r[eff_idx], r["fields_json"]
        )
        extra.update(resolver.resolve(r[0], r[loc_idx], r["fields_json"], r[1]))
        return (
            r[0], r[1],
            *(extra[f] if extra[f] is not None else "" for f in DERIVED_COLUMNS),
            *tuple(r)[2:len(EXPORT_COLUMNS)],
        )

    def write(path: Path, rows: list[sqlite3.Row]) -> None:
        with open(path, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows([derived(r) for r in rows])
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
