"""Read-only Illinois IEBS record correspondence across a source-key rebuild.

This relates source records, not legal filings or layoffs. An old notice may
contain several IEBS IDs under a mutable content key, and one IEBS ID may
appear under several old notice keys after a source revision.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from warnlive.normalize.engine import _dedupe_key

FIELDS = (
    "iebs_id", "old_notice_id", "old_version", "old_dedupe_key",
    "old_raw_record_hash", "old_employer_name",
    "old_workers", "new_notice_id", "new_dedupe_key",
    "new_source_notice_id", "new_employer_name", "new_workers", "status",
)


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build(old_db: Path, new_db: Path, out_csv: Path) -> dict[str, int]:
    """Write one row per old version with IEBS evidence, plus new-only IDs."""
    old, new = _connect(Path(old_db)), _connect(Path(new_db))
    try:
        candidate = {}
        for row in new.execute(
            "SELECT id,dedupe_key,source_identity,source_notice_id,"
            "employer_name,employees_affected FROM notices WHERE state='IL'"
        ):
            identity = row["source_identity"] or ""
            if not identity.startswith("IL:IEBS:") or not identity[8:]:
                raise ValueError(f"candidate IL notice {row['id']} has no IEBS identity")
            iebs_id = identity[8:]
            if row["dedupe_key"] != _dedupe_key({"state": "IL", "source_identity": identity}):
                raise ValueError(f"candidate IL notice {row['id']} has a legacy key")
            if iebs_id in candidate:
                raise ValueError(f"candidate repeats IEBS ID {iebs_id}")
            candidate[iebs_id] = dict(row)

        output = []
        old_ids = set()
        idless_versions = 0
        for row in old.execute(
            "SELECT n.id AS old_notice_id,n.dedupe_key AS old_dedupe_key,"
            "v.version AS old_version,"
            "v.raw_record_hash AS old_raw_record_hash,v.fields_json "
            "FROM notices n JOIN notice_versions v ON v.notice_id=n.id "
            "WHERE n.state='IL' ORDER BY n.id,v.version"
        ):
            fields = json.loads(row["fields_json"])
            raw = json.loads(fields.get("raw_extra") or "{}")
            iebs_id = (raw.get("IEBS Id") or "").strip()
            if not iebs_id:
                idless_versions += 1
                continue
            old_ids.add(iebs_id)
            match = candidate.get(iebs_id)
            output.append({
                "iebs_id": iebs_id,
                **{key: row[key] for key in (
                    "old_notice_id", "old_version", "old_dedupe_key",
                    "old_raw_record_hash",
                )},
                "old_employer_name": fields.get("employer_name"),
                "old_workers": fields.get("employees_affected"),
                "new_notice_id": match["id"] if match else "",
                "new_dedupe_key": match["dedupe_key"] if match else "",
                "new_source_notice_id": match["source_notice_id"] if match else "",
                "new_employer_name": match["employer_name"] if match else "",
                "new_workers": match["employees_affected"] if match else "",
                "status": "matched_iebs_id" if match else "absent_from_candidate",
            })
        for iebs_id in sorted(candidate.keys() - old_ids):
            match = candidate[iebs_id]
            output.append({
                "iebs_id": iebs_id,
                "new_notice_id": match["id"], "new_dedupe_key": match["dedupe_key"],
                "new_source_notice_id": match["source_notice_id"],
                "new_employer_name": match["employer_name"],
                "new_workers": match["employees_affected"],
                "status": "new_in_frozen_source",
            })
        output.sort(key=lambda item: (item["iebs_id"], str(item.get("old_notice_id", "")),
                                      str(item.get("old_version", ""))))
        out_csv = Path(out_csv)
        out_csv.parent.mkdir(parents=True, exist_ok=True)
        with out_csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(output)
        return {
            "old_versions_with_iebs_id": len(output) - len(candidate.keys() - old_ids),
            "old_versions_without_iebs_id": idless_versions,
            "old_distinct_iebs_ids": len(old_ids),
            "candidate_distinct_iebs_ids": len(candidate),
            "old_ids_absent_from_candidate": len(old_ids - candidate.keys()),
            "candidate_ids_absent_from_old_db": len(candidate.keys() - old_ids),
        }
    finally:
        old.close()
        new.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-db", type=Path, required=True)
    parser.add_argument("--new-db", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.old_db, args.new_db, args.out), sort_keys=True))


if __name__ == "__main__":
    main()
