"""Map released WI/FL update-row keys to the notices that now hold them.

An archived Wisconsin (or Florida) "update" row whose content exactly equals
an earlier row for the same employer and location is now a version of that
earlier notice (``normalize.entries.fold_identical_updates``). The update
row's released ``dedupe_key`` is therefore retired. This read-only tool
lists each retired key and its surviving key from a candidate database, and
checks the release invariant: every released key is either still a notice
in the candidate or listed here.

Inputs: the released export (``data/exports/warn_notices.csv``) and a
candidate SQLite database, opened read-only. Output: a review CSV under
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
    "employer_name", "location", "update_received_date", "parent_received_date",
    "effective_date", "employees_affected", "layoff_type", "basis",
)
STATES = ("WI", "FL")


def _connect(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(Path(path).resolve().as_posix(), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def transitions(candidate_db: Path) -> list[dict]:
    """Retired update-row keys and the candidate notice version holding each."""
    conn = _connect(candidate_db)
    try:
        rows = []
        for row in conn.execute(
            "SELECT n.state, n.dedupe_key, n.source_details AS current_details, "
            "v.version, v.fields_json FROM notices n "
            "JOIN notice_versions v ON v.notice_id = n.id "
            "WHERE n.state IN (%s) AND v.fields_json LIKE '%%version_of%%' "
            "ORDER BY n.state, n.dedupe_key, v.version" % ",".join("?" * len(STATES)),
            STATES,
        ):
            fields = json.loads(row["fields_json"])
            details = json.loads(fields.get("source_details") or "{}")
            version_of = details.get("version_of")
            if not version_of:
                continue
            first = json.loads(conn.execute(
                "SELECT v.fields_json FROM notice_versions v JOIN notices n "
                "ON n.id = v.notice_id WHERE n.dedupe_key = ? AND v.version = 1",
                (row["dedupe_key"],),
            ).fetchone()[0])
            parent = json.loads(first.get("source_details") or "{}")
            rows.append({
                "state": row["state"],
                "retired_dedupe_key": version_of["retired_dedupe_key"],
                "surviving_dedupe_key": row["dedupe_key"],
                "surviving_version": row["version"],
                "employer_name": fields.get("employer_name"),
                "location": fields.get("location"),
                "update_received_date": details.get("agency_received_date"),
                "parent_received_date": parent.get("agency_received_date"),
                "effective_date": fields.get("effective_date"),
                "employees_affected": fields.get("employees_affected"),
                "layoff_type": fields.get("layoff_type"),
                "basis": version_of["basis"],
            })
        return rows
    finally:
        conn.close()


def check(released_csv: Path, candidate_db: Path, mapping: list[dict]) -> dict:
    """Released keys absent from the candidate, split by whether the map retires them."""
    with Path(released_csv).open(newline="", encoding="utf-8") as stream:
        released = {row["dedupe_key"]: row["state"] for row in csv.DictReader(stream)}
    conn = _connect(candidate_db)
    try:
        candidate = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    finally:
        conn.close()
    retired = {row["retired_dedupe_key"] for row in mapping}
    missing = sorted(key for key in released if key not in candidate)
    unmapped = [key for key in missing if key not in retired]
    return {
        "released_keys": len(released),
        "retired_by_map": sum(key in retired for key in missing),
        "retired_keys_not_released": sorted(retired - set(released)),
        "missing_unmapped": len(unmapped),
        "missing_unmapped_by_state": {
            state: sum(released[key] == state for key in unmapped)
            for state in sorted({released[key] for key in unmapped})
        },
        "missing_unmapped_keys": unmapped,
    }


def write(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--released", type=Path, default=Path("data/exports/warn_notices.csv"))
    parser.add_argument("--out", type=Path,
                        default=Path("data/review/wi-fl-update-transition-2026-09-29.csv"))
    parser.add_argument("--check-only", action="store_true",
                        help="Report the released-key invariant without writing the CSV")
    args = parser.parse_args()
    mapping = transitions(args.candidate)
    if not args.check_only:
        write(mapping, args.out)
    result = check(args.released, args.candidate, mapping)
    result["missing_unmapped_keys"] = result["missing_unmapped_keys"][:20]
    print(json.dumps({"transitions": len(mapping), **result}, indent=2, sort_keys=True))
