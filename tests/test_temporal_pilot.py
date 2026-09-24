"""Temporal pilot is deterministic and does not modify the candidate database."""

import csv
import hashlib
import json
import sqlite3

from warnlive.enrich.temporal_pilot import run


def _fixture(tmp_path):
    artifact = tmp_path / "source.txt"
    artifact.write_text("official record\n")
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({
        "schema_version": 1,
        "sources": [{"source_id": "official", "url": "https://example.gov/filing",
                     "local_path": "source.txt", "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                     "retrieved_at": "2026-09-23T00:00:00Z", "published_date": "2020-04-01"}],
        "entities": [
            {"entity_id": "child", "name": "Child", "identifiers": {}},
            {"entity_id": "parent", "name": "Parent", "identifiers": {}},
        ],
        "identities": [{"assertion_id": "identity", "source_notice_id": "src-1", "state": "CA",
                        "filed_name": "Child", "entity_id": "child", "source_id": "official"}],
        "relationships": [{"assertion_id": "ownership", "child_entity_id": "child",
                           "parent_entity_id": "parent", "relationship_type": "direct_parent",
                           "source_id": "official", "temporal_scope": {"kind": "interval",
                           "from": "2020-01-01", "through": "2020-12-31"}}],
    }))
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps([{"state": "CA", "source_notice_id": "src-1"}]))
    db = tmp_path / "candidate.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notices (id INTEGER, state TEXT, source_notice_id TEXT, "
                 "employer_name TEXT, notice_date TEXT, effective_date TEXT, "
                 "effective_date_end TEXT, notice_date_precision TEXT, "
                 "effective_date_precision TEXT, effective_date_end_precision TEXT, "
                 "notice_date_basis TEXT, effective_date_basis TEXT, effective_date_end_basis TEXT, "
                 "source_details TEXT)")
    conn.execute("INSERT INTO notices VALUES (1, 'CA', 'src-1', 'Child', "
                 "'2020-03-01', '2020-04-01', '2020-07-01', 'month', 'day', 'day', "
                 "'reported', 'reported', 'reported', NULL)")
    conn.commit()
    conn.close()
    return db, ledger, selection


def test_pilot_emits_three_date_events_and_stable_manifest(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    output = tmp_path / "output"
    first = run(db, ledger, selection, output)
    csv_bytes = (output / "temporal_entity_review.csv").read_bytes()
    manifest_bytes = (output / "manifest.json").read_bytes()
    assert first["selected_notices"] == 1 and first["review_rows"] == 15
    with (output / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    direct = [r for r in rows if r["relationship_type"] == "direct_parent"]
    assert [(r["event"], r["as_of_date"], r["relationship_status"]) for r in direct] == [
        ("notice", "2020-03-01", "date_not_precise"),
        ("effective_start", "2020-04-01", "accepted"),
        ("effective_end", "2020-07-01", "accepted"),
    ]
    assert all(r["relationship_status"] == "unknown" for r in rows
               if r["relationship_type"] == "ultimate_parent" and r["event"] != "notice")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert run(db, ledger, selection, output) == first
    assert (output / "temporal_entity_review.csv").read_bytes() == csv_bytes
    assert (output / "manifest.json").read_bytes() == manifest_bytes


def test_unmarked_full_iso_day_does_not_grant_as_of_ownership(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE notices SET notice_date_precision = NULL")
    conn.commit()
    conn.close()
    run(db, ledger, selection, tmp_path / "output")
    with (tmp_path / "output" / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    notice = next(r for r in rows if r["event"] == "notice" and r["relationship_type"] == "direct_parent")
    assert notice["as_of_date"] == "2020-03-01"
    assert notice["date_precision"] == "unknown"
    assert notice["relationship_status"] == "date_not_precise"


def test_unmarked_effective_dates_do_not_grant_parent(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE notices SET effective_date_precision = NULL, "
                 "effective_date_end_precision = NULL")
    conn.commit()
    conn.close()
    run(db, ledger, selection, tmp_path / "output")
    with (tmp_path / "output" / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    effective = [r for r in rows if r["relationship_type"] == "direct_parent"
                 and r["event"] in ("effective_start", "effective_end")]
    assert len(effective) == 2
    assert all(r["date_status"] == "precision_unmarked" and
               r["relationship_status"] == "date_not_precise" for r in effective)


def test_unassessed_date_basis_does_not_grant_parent(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE notices SET effective_date_basis = NULL")
    conn.commit()
    conn.close()
    run(db, ledger, selection, tmp_path / "output")
    with (tmp_path / "output" / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    start = next(r for r in rows if r["event"] == "effective_start"
                 and r["relationship_type"] == "direct_parent")
    assert start["date_precision"] == "day"
    assert start["date_basis"] == "unassessed"
    assert start["date_status"] == "basis_unassessed"
    assert start["relationship_status"] == "date_not_precise"


def test_unresolved_date_review_blocks_historical_parent(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE notices SET source_details = ?", (json.dumps({
        "effective_date_end_status": "before_start_review"}),))
    conn.commit()
    conn.close()
    run(db, ledger, selection, tmp_path / "output")
    with (tmp_path / "output" / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    effective = [r for r in rows if r["relationship_type"] == "direct_parent"
                 and r["event"] in ("effective_start", "effective_end")]
    assert len(effective) == 2
    assert all(r["date_status"] == "date_review_hold" and
               r["relationship_status"] == "date_review_hold" for r in effective)


def test_manifest_fingerprints_wal_visible_snapshot(tmp_path):
    db, ledger, selection = _fixture(tmp_path)
    writer = sqlite3.connect(db)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    first = run(db, ledger, selection, tmp_path / "first")
    main_file_before = hashlib.sha256(db.read_bytes()).hexdigest()
    writer.execute("UPDATE notices SET effective_date = '2021-04-01', "
                   "effective_date_end = '2021-07-01'")
    writer.commit()
    assert (tmp_path / "candidate.sqlite-wal").stat().st_size > 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == main_file_before
    second = run(db, ledger, selection, tmp_path / "second")
    writer.close()
    assert first["candidate_db_sha256"] != second["candidate_db_sha256"]
    assert second["candidate_hash_basis"] == "sqlite_backup_snapshot_including_committed_wal"
    with (tmp_path / "second" / "temporal_entity_review.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    effective = next(r for r in rows if r["event"] == "effective_start" and r["relationship_type"] == "direct_parent")
    assert effective["as_of_date"] == "2021-04-01"
    assert effective["relationship_status"] == "unknown"
