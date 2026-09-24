import csv
import json

from warnlive.migrate.il_transition import build
from warnlive.normalize.engine import _dedupe_key
from warnlive.store import db, dedupe


def _row(key, raw_hash, name, iebs_id, identity=None):
    return {
        "state": "IL", "dedupe_key": key, "raw_record_hash": raw_hash,
        "source_identity": identity, "employer_name": name,
        "location": "Chicago", "notice_date": "2025-11-03",
        "effective_date": None, "employees_affected": 100,
        "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": raw_hash,
        "raw_extra": json.dumps({"IEBS Id": iebs_id}),
    }


def test_illinois_transition_maps_two_old_keys_to_one_source_record(tmp_path):
    old_path, new_path = tmp_path / "old.sqlite", tmp_path / "new.sqlite"
    old, new = db.connect(old_path), db.connect(new_path)
    db.init_db(old)
    db.init_db(new)
    identity = "IL:IEBS:20251104001"
    key = _dedupe_key({"state": "IL", "source_identity": identity})
    dedupe.ingest(old, [
        _row("legacy-one", "hash-a", "Norvax", "20251104001"),
        _row("legacy-two", "hash-b", "Norvax/GoHealth", "20251104001"),
        _row("later-only", "hash-c", "Later", "20260918003"),
    ], "2026-09-22")
    dedupe.ingest(new, [
        _row(key, "hash-a", "Norvax", "20251104001", identity),
    ], "2026-09-22")
    old.close()
    new.close()
    output = tmp_path / "bridge.csv"
    stats = build(old_path, new_path, output)
    rows = list(csv.DictReader(output.open()))
    assert stats["old_distinct_iebs_ids"] == 2
    assert stats["old_ids_absent_from_candidate"] == 1
    assert len(rows) == 3
    matched = [row for row in rows if row["status"] == "matched_iebs_id"]
    assert len({row["old_notice_id"] for row in matched}) == 2
    assert len({row["new_notice_id"] for row in matched}) == 1
    assert [row["iebs_id"] for row in rows if row["status"] == "absent_from_candidate"] == [
        "20260918003"
    ]
