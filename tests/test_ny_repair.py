import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from warnlive.migrate import ny_repair
from warnlive.normalize.engine import _dedupe_key, _record_hash
from warnlive.store.db import connect, init_db
from warnlive.store.dedupe import VERSIONED_FIELDS


NY_SOURCE = Path(__file__).resolve().parents[1] / "data/source_snapshots/ny"


def _sha(raw):
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _fixture(tmp_path, monkeypatch, *, two=False):
    conn = connect(tmp_path / "candidate.sqlite")
    init_db(conn)
    artifact_dir = tmp_path / "ny"
    artifact_dir.mkdir()
    (artifact_dir / "filing.pdf").write_bytes(b"reviewed filing bytes")
    official = []
    decisions = []
    for index in range(2 if two else 1):
        source_id = f"bln-{index}"
        name = f"Employer {index}"
        raw = {"hash_id": source_id, "postal_code": "NY", "company": name,
               "location": "", "notice_date": "2024-09-30", "effective_date": ""}
        fields = {field: None for field in VERSIONED_FIELDS}
        fields.update({"state": "NY", "employer_name": name, "location": None,
                       "notice_date": "2024-09-30", "effective_date": None,
                       "layoff_type": "unknown", "is_amendment": 0})
        key = _dedupe_key({**fields, "source_identity": None})
        rec = {**fields, "raw_extra": json.dumps(raw, sort_keys=True, ensure_ascii=False)}
        conn.execute(
            "INSERT INTO notices (dedupe_key,state,employer_name,location,notice_date,"
            "effective_date,layoff_type,is_amendment,source_notice_id,first_seen) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (key, "NY", name, None, "2024-09-30", None, "unknown", 0,
             source_id, "2024-10-01"),
        )
        notice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO notice_versions (notice_id,version,raw_record_hash,fields_json,observed_at) "
            "VALUES (?,?,?,?,?)",
            (notice_id, 1, _record_hash(rec), json.dumps(rec, sort_keys=True), "2024-10-01"),
        )
        official.append({"source_row_id": f"official-{index}", "row_sha256": f"sha-{index}",
                         "source_url": "https://example.com/agency.csv",
                         "notice_date": "2024-09-16", "effective_date": "2024-09-30"})
        decisions.append({
            "bln_source_notice_id": source_id, "bln_row_sha256": _sha(raw),
            "expected_employer_name": name, "expected_location": None,
            "expected_dedupe_key": key, "expected_notice_date": "2024-09-30",
            "expected_effective_date": None, "official_source_row_id": f"official-{index}",
            "official_row_sha256": f"sha-{index}", "new_notice_date": "2024-09-16",
            "new_effective_date": "2024-09-30", "new_effective_date_end": "2024-09-30",
            "official_document_path": "filing.pdf",
            "official_document_sha256": hashlib.sha256(b"reviewed filing bytes").hexdigest(),
        })
    conn.commit()
    monkeypatch.setattr(ny_repair, "read_artifacts", lambda _: official)
    return conn, decisions, artifact_dir


def test_reviewed_repair_versions_dates_preserves_raw_and_is_idempotent(tmp_path, monkeypatch):
    conn, decisions, artifacts = _fixture(tmp_path, monkeypatch)
    before = conn.execute("SELECT id, fields_json FROM notice_versions").fetchone()
    result = ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23")
    assert result == {"applied": ["bln-0"], "already_applied": [], "held_collisions": {}}
    row = conn.execute("SELECT * FROM notices").fetchone()
    assert (row["notice_date"], row["effective_date"], row["effective_date_end"]) == (
        "2024-09-16", "2024-09-30", "2024-09-30")
    assert row["current_version"] == 2
    assert row["notice_date_precision"] == "day"
    assert row["notice_date_basis"] == "reported"
    assert (row["effective_date_precision"], row["effective_date_basis"]) == ("day", "reported")
    assert (row["effective_date_end_precision"], row["effective_date_end_basis"]) == ("day", "reported")
    assert json.loads(row["source_details"])["ny_reviewed_repair"]["official_document_path"] == "filing.pdf"
    versions = conn.execute("SELECT * FROM notice_versions ORDER BY version").fetchall()
    assert len(versions) == 2
    assert versions[0]["fields_json"] == before["fields_json"]
    assert json.loads(versions[1]["fields_json"])["raw_extra"] == json.loads(before["fields_json"])["raw_extra"]
    assert ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23") == {
        "applied": [], "already_applied": ["bln-0"], "held_collisions": {}}
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_drift_aborts_entire_batch(tmp_path, monkeypatch):
    conn, decisions, artifacts = _fixture(tmp_path, monkeypatch, two=True)
    decisions[1]["expected_employer_name"] = "wrong"
    with pytest.raises(ValueError, match="expected field drift"):
        ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23")
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE notice_date='2024-09-16'").fetchone()[0] == 0


def test_existing_key_collision_is_held(tmp_path, monkeypatch):
    conn, decisions, artifacts = _fixture(tmp_path, monkeypatch)
    key = _dedupe_key({"state": "NY", "employer_name": "Employer 0",
                       "location": None, "notice_date": "2024-09-16"})
    conn.execute("INSERT INTO notices (dedupe_key,state,employer_name,notice_date,first_seen) "
                 "VALUES (?,?,?,?,?)", (key, "NY", "other", "2024-09-16", "2024-01-01"))
    result = ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23")
    assert result["applied"] == []
    assert result["held_collisions"]["bln-0"]["proposed_dedupe_key"] == key
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 1


def test_pinned_filing_bytes_required_for_end(tmp_path, monkeypatch):
    conn, decisions, artifacts = _fixture(tmp_path, monkeypatch)
    (artifacts / "filing.pdf").write_bytes(b"different")
    with pytest.raises(ValueError, match="official document drift"):
        ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23")
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 1


def test_version_failure_rolls_back_all_repair_updates(tmp_path, monkeypatch):
    conn, decisions, artifacts = _fixture(tmp_path, monkeypatch, two=True)
    original = ny_repair.append_repair_version
    calls = 0

    def fail_second(connection, notice_id, observed_at):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic version failure")
        return original(connection, notice_id, observed_at)

    monkeypatch.setattr(ny_repair, "append_repair_version", fail_second)
    with pytest.raises(RuntimeError, match="synthetic version failure"):
        ny_repair.apply_reviewed_repairs(conn, decisions, artifacts, "2026-09-23")
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE notice_date='2024-09-16'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_pinned_first_transit_decision_replays_once(tmp_path):
    decisions = json.loads((NY_SOURCE / "reviewed_date_repairs.json").read_text())
    decision, = (item for item in decisions if item["official_document_path"].endswith(
        "first-transit-2023-0298.pdf"))
    assert (date.fromisoformat(decision["new_effective_date"])
            - date.fromisoformat(decision["new_notice_date"])).days == 101
    assert decision["new_effective_date_end"] == decision["new_effective_date"]

    # This is the pinned BLN source record, not a fabricated date pair.
    raw = {
        "company": "First Transit, Inc. a subsidiary of Transdev, North America Inc.",
        "effective_date": "", "estimated_amendments": "0",
        "first_inserted_date": "2024-03-28 14:00:37.051757+00:00",
        "hash_id": decision["bln_source_notice_id"], "is_amendment": "False",
        "is_closure": "", "is_superseded": "False", "is_temporary": "",
        "jobs": "", "last_updated_date": "2024-03-28 14:00:37.051757+00:00",
        "likely_ancestor": "", "location": "", "notice_date": "2024-03-27",
        "postal_code": "NY",
    }
    assert _sha(raw) == decision["bln_row_sha256"]
    assert _dedupe_key({"state": "NY", "employer_name": raw["company"],
                        "location": None, "notice_date": "2024-03-27"}) == decision["expected_dedupe_key"]
    conn = connect(tmp_path / "candidate.sqlite")
    init_db(conn)
    conn.execute(
        "INSERT INTO notices (dedupe_key,state,employer_name,location,notice_date,"
        "effective_date,layoff_type,is_amendment,source_notice_id,first_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (decision["expected_dedupe_key"], "NY", raw["company"], None,
         "2024-03-27", None, "unknown", 0, raw["hash_id"], "2026-09-23"),
    )
    notice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    fields = {field: None for field in VERSIONED_FIELDS}
    fields.update({"state": "NY", "employer_name": raw["company"],
                   "notice_date": "2024-03-27", "effective_date": None,
                   "layoff_type": "unknown", "is_amendment": 0,
                   "raw_extra": json.dumps(raw, sort_keys=True, ensure_ascii=False)})
    conn.execute(
        "INSERT INTO notice_versions (notice_id,version,raw_record_hash,fields_json,observed_at) "
        "VALUES (?,?,?,?,?)",
        (notice_id, 1, _record_hash(fields), json.dumps(fields, sort_keys=True), "2026-09-23"),
    )
    conn.commit()
    assert ny_repair.apply_reviewed_repairs(conn, [decision], NY_SOURCE, "2026-09-23") == {
        "applied": [raw["hash_id"]], "already_applied": [], "held_collisions": {}}
    repaired = conn.execute("SELECT * FROM notices WHERE id=?", (notice_id,)).fetchone()
    assert (repaired["notice_date"], repaired["effective_date"],
            repaired["effective_date_end"]) == ("2024-03-21", "2024-06-30", "2024-06-30")
    assert repaired["notice_date_precision"] == "day"
    assert (repaired["effective_date_precision"], repaired["effective_date_basis"]) == ("day", "reported")
    assert (repaired["effective_date_end_precision"], repaired["effective_date_end_basis"]) == ("day", "reported")
    assert repaired["current_version"] == 2
    assert conn.execute("SELECT COUNT(*) FROM notice_versions WHERE notice_id=?",
                        (notice_id,)).fetchone()[0] == 2
    assert ny_repair.apply_reviewed_repairs(conn, [decision], NY_SOURCE, "2026-09-23") == {
        "applied": [], "already_applied": [raw["hash_id"]], "held_collisions": {}}
