"""Map released NY dashboard keys that became versions of a control-number filing.

In v1.1.1 some NY dashboard rows (``agency/ny_annual``) were admitted as their
own notices because the control-number filing they describe was held (its
detail pages shared one key with different content). ``entries
.control_number_versions`` now admits that filing, and a dashboard row that
matches exactly one such filing on employer group, notice date, workers and
county becomes that filing's next version (``ny_annual_source``,
``source_details.ny_dashboard_correspondence``). The dashboard row's released
``dedupe_key`` is therefore retired in favour of the filing's key.

This read-only tool lists each retired key with its surviving key and the
evidence, and checks that every released NY key absent from the candidate is
covered. Inputs: the released export (``data/exports/warn_notices.csv``) and
a candidate SQLite database, opened read-only. Output: a review CSV under
``data/review/``. No network access.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

FIELDS = (
    "state", "retired_dedupe_key", "surviving_dedupe_key", "surviving_version",
    "surviving_source_notice_id", "employer_name", "surviving_employer_name",
    "notice_date", "employees_affected", "dashboard_county", "surviving_location",
    "dashboard_source_row", "basis",
)

csv.field_size_limit(10**9)


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(Path(path).resolve().as_posix(), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _released(released_csv: Path) -> dict[str, dict]:
    with Path(released_csv).open(newline="", encoding="utf-8") as stream:
        return {row["dedupe_key"]: row for row in csv.DictReader(stream)}


def transitions(released_csv: Path, candidate_db: Path) -> list[dict]:
    """Released NY dashboard keys now held as a correspondence version."""
    released = _released(released_csv)
    by_source_row = {row["source_notice_id"]: row for row in released.values()
                     if row["state"] == "NY" and row["source_notice_id"]}
    conn = _connect(candidate_db)
    try:
        candidate = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
        rows = []
        for row in conn.execute(
            "SELECT n.dedupe_key, v.version, v.fields_json FROM notices n "
            "JOIN notice_versions v ON v.notice_id = n.id "
            "WHERE n.state = 'NY' AND v.fields_json LIKE '%ny_dashboard_correspondence%' "
            "ORDER BY n.dedupe_key, v.version"
        ):
            fields = json.loads(row["fields_json"])
            evidence = json.loads(fields.get("source_details") or "{}").get(
                "ny_dashboard_correspondence")
            if not evidence:
                continue
            old = by_source_row.get(evidence["source_row"])
            if old is None or old["dedupe_key"] in candidate:
                continue
            cells = evidence.get("raw_cells") or []
            rows.append({
                "state": "NY",
                "retired_dedupe_key": old["dedupe_key"],
                "surviving_dedupe_key": row["dedupe_key"],
                "surviving_version": row["version"],
                "surviving_source_notice_id": fields.get("source_notice_id"),
                "employer_name": old["employer_name"],
                "surviving_employer_name": fields.get("employer_name"),
                "notice_date": old["notice_date"],
                "employees_affected": old["employees_affected"],
                "dashboard_county": cells[5] if len(cells) > 5 else "",
                "surviving_location": fields.get("location"),
                "dashboard_source_row": evidence["source_row"],
                "basis": evidence["basis"],
            })
        rows.sort(key=lambda item: item["retired_dedupe_key"])
        return rows
    finally:
        conn.close()


def check(released_csv: Path, candidate_db: Path, mapping: list[dict]) -> dict:
    """Released NY keys absent from the candidate, split by whether the map covers them."""
    released = _released(released_csv)
    conn = _connect(candidate_db)
    try:
        candidate = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    finally:
        conn.close()
    retired = {row["retired_dedupe_key"] for row in mapping}
    missing = sorted(key for key, row in released.items()
                     if row["state"] == "NY" and key not in candidate)
    return {
        "released_ny_keys": sum(row["state"] == "NY" for row in released.values()),
        "retired_by_map": sum(key in retired for key in missing),
        "retired_keys_not_released": sorted(retired - set(released)),
        "surviving_keys_missing": sorted(
            row["surviving_dedupe_key"] for row in mapping
            if row["surviving_dedupe_key"] not in candidate),
        "missing_unmapped_keys": [key for key in missing if key not in retired],
    }


def write(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--released", type=Path, default=Path("data/exports/warn_notices.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("data/review/ny-dashboard-correspondence-transition-2026-09-30.csv"))
    parser.add_argument("--check-only", action="store_true",
                        help="Report the released-key invariant without writing the CSV")
    args = parser.parse_args()
    mapping = transitions(args.released, args.candidate)
    if not args.check_only:
        write(mapping, args.out)
    result = check(args.released, args.candidate, mapping)
    print(json.dumps({"transitions": len(mapping), **result}, indent=2, sort_keys=True))
