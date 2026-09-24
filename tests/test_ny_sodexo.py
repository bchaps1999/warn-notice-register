"""Reviewed one-event New York duplicate and date-role replay."""

import csv
import hashlib
import json
import tarfile
from pathlib import Path

import pytest

from warnlive.migrate.ny_repair import (
    apply_reviewed_event_repairs, preflight_reviewed_event_decisions,
)
from warnlive.store.db import connect, init_db
from warnlive.store.dedupe import VERSIONED_FIELDS
from warnlive.verify.timing import audit


ROOT = Path(__file__).resolve().parents[1]
NY = ROOT / "data/source_snapshots/ny"
BUNDLE = ROOT / "data/source_snapshots/2026-09-23-ia-la-ny-first-transit-reviewed.tar.gz"
DECISION = json.loads((NY / "reviewed_event_decisions.json").read_text())[0]


def _source_csv(tmp_path):
    path = tmp_path / "bln_integrated.csv"
    with tarfile.open(BUNDLE, "r:gz") as archive:
        path.write_bytes(archive.extractfile("backfill/bln_integrated.csv").read())
    return path


def _candidate(tmp_path):
    conn = connect(tmp_path / "candidate.sqlite")
    init_db(conn)
    item = DECISION["survivor"]
    with tarfile.open(BUNDLE, "r:gz") as archive:
        import csv
        import io
        rows = csv.DictReader(io.TextIOWrapper(archive.extractfile("backfill/bln_integrated.csv")))
        raw = next(row for ordinal, row in enumerate(rows, start=1)
                   if ordinal == item["bln_source_row"])
    conn.execute(
        "INSERT INTO notices (dedupe_key,state,employer_name,location,notice_date,"
        "effective_date,layoff_type,is_amendment,source_notice_id,first_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (item["expected_dedupe_key"], "NY", item["expected_employer_name"], None,
         item["expected_notice_date"], None, "unknown", 0,
         item["bln_source_notice_id"], "2026-09-23"),
    )
    notice_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    fields = {field: None for field in VERSIONED_FIELDS}
    fields.update({
        "state": "NY", "employer_name": item["expected_employer_name"],
        "notice_date": item["expected_notice_date"], "layoff_type": "unknown",
        "is_amendment": 0, "raw_extra": json.dumps(raw, sort_keys=True, ensure_ascii=False),
    })
    conn.execute(
        "INSERT INTO notice_versions (notice_id,version,raw_record_hash,fields_json,observed_at) "
        "VALUES (?,?,?,?,?)",
        (notice_id, 1, hashlib.sha256(b"source").hexdigest(),
         json.dumps(fields, sort_keys=True), "2026-09-23"),
    )
    conn.commit()
    return conn


def test_sodexo_duplicate_excluded_before_ingestion_and_survivor_repaired(tmp_path):
    conn = _candidate(tmp_path)
    excluded = preflight_reviewed_event_decisions(_source_csv(tmp_path), NY, [DECISION], conn)
    duplicate = DECISION["duplicate"]["bln_source_notice_id"]
    assert list(excluded) == [duplicate]
    assert excluded[duplicate]["survivor_source_notice_id"] == DECISION["survivor"]["bln_source_notice_id"]
    result = apply_reviewed_event_repairs(conn, [DECISION], NY, "2026-09-23")
    assert result["applied"] == [DECISION["survivor"]["bln_source_notice_id"]]
    row = conn.execute("SELECT * FROM notices").fetchone()
    assert (row["notice_date"], row["effective_date"], row["effective_date_end"]) == (
        "2022-06-21", "2022-06-30", None,
    )
    assert (row["notice_date_precision"], row["notice_date_basis"]) == ("day", "reported")
    assert (row["effective_date_precision"], row["effective_date_basis"]) == ("day", "reported")
    assert row["effective_date_end_precision"] is None
    assert row["employees_affected"] is None
    provenance = json.loads(row["source_details"])["ny_reviewed_repair"]
    assert provenance["effective_date_role"] == "reported_closing_and_action_start"
    assert provenance["employee_separation_phases"][1]["date"] == "2022-07-28"
    assert apply_reviewed_event_repairs(conn, [DECISION], NY, "2026-09-23")["already_applied"] == [
        DECISION["survivor"]["bln_source_notice_id"]]
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
    conn.commit()
    audit(tmp_path / "candidate.sqlite", tmp_path / "timing")
    with (tmp_path / "timing" / "source_supported_start_timing_cohort.csv").open() as fh:
        timing_rows = list(csv.DictReader(fh))
    assert len(timing_rows) == 1
    assert timing_rows[0]["days_notice_to_start"] == "9"
    assert timing_rows[0]["effective_date_role"] == "reported_closing_and_action_start"
    assert json.loads(timing_rows[0]["employee_separation_phases"])[1]["date"] == "2022-07-28"


def test_sodexo_source_or_candidate_drift_fails_closed(tmp_path):
    conn = _candidate(tmp_path)
    source = _source_csv(tmp_path)
    tampered = json.loads(json.dumps(DECISION))
    tampered["duplicate"]["bln_row_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="raw-row drift"):
        preflight_reviewed_event_decisions(source, NY, [tampered], conn)
    conn.execute("UPDATE notices SET employer_name='Different' ")
    with pytest.raises(ValueError, match="expected field drift"):
        apply_reviewed_event_repairs(conn, [DECISION], NY, "2026-09-23")


def test_sodexo_document_bytes_are_pinned(tmp_path):
    conn = _candidate(tmp_path)
    source = _source_csv(tmp_path)
    tampered = json.loads(json.dumps(DECISION))
    tampered["official_document_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="official document drift"):
        preflight_reviewed_event_decisions(source, NY, [tampered], conn)
