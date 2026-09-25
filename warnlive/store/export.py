"""Export consolidated + per-state CSVs from SQLite (never from raw files)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

EXPORT_COLUMNS = [
    "state",
    "employer_name",
    "location",
    "site_address",
    "notice_date",
    "notice_date_precision",
    "notice_date_basis",
    "effective_date",
    "effective_date_precision",
    "effective_date_basis",
    "effective_date_end",
    "effective_date_end_precision",
    "effective_date_end_basis",
    "source_identity",
    "source_details",
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

OBSERVATION_COLUMNS = [
    "source_bundle_sha256", "state", "observation_kind", "source_artifact",
    "source_row", "source_row_sha256", "admission_status",
    "notice_id", "notice_dedupe_key", "deterministic_json", "raw_json",
]


def export_source_observations(conn: sqlite3.Connection, export_dir: Path) -> int:
    """Export official observations, including excluded rows, apart from notices."""
    columns = ["n.dedupe_key AS notice_dedupe_key" if name == "notice_dedupe_key"
               else "CASE WHEN s.admission_status = 'event_review' "
                    "THEN 'event_unresolved' ELSE s.admission_status END AS admission_status"
                    if name == "admission_status" else f"s.{name}"
               for name in OBSERVATION_COLUMNS]
    rows = conn.execute(
        f"SELECT {', '.join(columns)} FROM source_observations s "
        "LEFT JOIN notices n ON n.id=s.notice_id "
        "ORDER BY s.source_bundle_sha256, s.source_artifact, s.source_row"
    ).fetchall()
    path = Path(export_dir) / "source_observations.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(OBSERVATION_COLUMNS)
        writer.writerows(tuple(row) for row in rows)
    return len(rows)


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
        columns = [
            "COALESCE(notice_date_precision, CASE WHEN state = 'NJ' "
            "AND notice_date IS NOT NULL THEN 'month' END) AS notice_date_precision"
            if name == "notice_date_precision" else
            "COALESCE(notice_date_basis, CASE WHEN state = 'NJ' "
            "AND notice_date IS NOT NULL THEN 'inferred_year_from_effective_date' END) "
            "AS notice_date_basis"
            if name == "notice_date_basis" else name
            for name in EXPORT_COLUMNS
        ]
        return conn.execute(
            f"SELECT {', '.join(columns)}, "
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
    from warnlive.enrich.notice_quality import (
        FIELDS as QUALITY_COLUMNS, _quality, geo_location, project,
    )

    annotator = Annotator()
    annotator.prime(conn)
    # Geography belongs to the notice rather than the employer, so it is
    # resolved separately and merged in beside the identity columns.
    resolver = Resolver()
    DERIVED_COLUMNS = IDENTITY_COLUMNS + PLACE_COLUMNS + list(QUALITY_COLUMNS)
    header = EXPORT_COLUMNS[:2] + DERIVED_COLUMNS + EXPORT_COLUMNS[2:]
    date_idx = EXPORT_COLUMNS.index("notice_date")
    eff_idx = EXPORT_COLUMNS.index("effective_date")
    loc_idx = EXPORT_COLUMNS.index("location")

    def derived(r: sqlite3.Row) -> tuple:
        row = dict(r)
        extra = annotator.annotate(
            r[1], r[date_idx] or r[eff_idx], r["fields_json"],
            state=r[0], location=r[loc_idx],
        )
        quality = _quality(row)
        if (quality.get("sites") or quality.get("location_role") == "employer_mailing"
                or quality.get("status") == "site_address_ambiguous"):
            extra.update(resolver.resolve(r[0], geo_location(row)))
        else:
            extra.update(resolver.resolve(r[0], r[loc_idx], r["fields_json"], r[1]))
        extra.update(project(row))
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
    observation_count = export_source_observations(conn, export_dir)
    counts[str(export_dir / "source_observations.csv")] = observation_count
    return counts
