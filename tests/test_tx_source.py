"""Official Texas workbook date roles and fail-closed correspondence."""

import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

from warnlive.migrate.tx_source import HEADERS, apply_date_evidence, read_artifacts
from warnlive.normalize.engine import normalize_file
from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "data/source_snapshots/tx"
RAW = ROOT / "workdir/raw/tx.csv"


def test_texas_workbooks_pin_all_source_rows_and_republished_year():
    rows = read_artifacts(ARTIFACTS, RAW)
    assert len(rows) == 2475
    assert Counter(row["source_artifact"] for row in rows) == {
        f"agency/tx/{year}.xlsx": count for year, count in
        [(2020, 1209), (2021, 123), (2022, 86), (2023, 225),
         (2024, 464), (2025, 289), (2026, 79)]
    }
    assert sum(row["source_artifact"].endswith("2024.xlsx")
               and row["notice_date"].startswith("2023-") for row in rows) == 225
    assert tuple(rows[0]["source_fields"]) == HEADERS


def test_texas_workbook_hash_header_and_raw_correspondence_fail_closed(tmp_path):
    directory = tmp_path / "tx"
    shutil.copytree(ARTIFACTS, directory)
    original = (directory / "2026.xlsx").read_bytes()
    (directory / "2026.xlsx").write_bytes(original + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(directory, RAW)
    (directory / "2026.xlsx").write_bytes(original)
    raw = tmp_path / "tx.csv"
    raw.write_bytes(RAW.read_bytes().replace(b"Temco Logistics", b"Temco LogisticS", 1))
    with pytest.raises(ValueError, match="raw CSV drift"):
        read_artifacts(directory, raw)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["raw_csv_bytes"] = len(raw.read_bytes())
    manifest["raw_csv_sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="correspondence drift"):
        read_artifacts(directory, raw)


def test_texas_date_annotation_requires_current_raw_and_canonical_match(tmp_path):
    norm = normalize_file("tx", ROOT / "workdir/raw", load_registry()["tx"].source_url)
    temco = next(row for row in norm.records if row["employer_name"] == "Temco Logistics"
                 and row["notice_date"] == "2026-07-22")
    corrected = next(row for row in norm.records if row["employer_name"] == "Autobahn Imports"
                     and row["notice_date"] == "2020-03-24")
    db = tmp_path / "candidate.sqlite"
    conn = db_mod.connect(db)
    try:
        db_mod.init_db(conn)
        ingest(conn, [temco, corrected], observed_at="2026-09-22")
        exceptions = []
        report = apply_date_evidence(conn, ARTIFACTS, RAW, "2026-09-22", exceptions)
        assert report["matched_current_notices"] == 2
        assert report["reported_effective_dates"] == 1
        assert report["held_corrected_effective_dates"] == 1
        rows = {row["employer_name"]: row for row in
                conn.execute("SELECT * FROM notices WHERE state='TX'")}
        assert rows["Temco Logistics"]["notice_date_basis"] == "reported"
        assert rows["Temco Logistics"]["effective_date_basis"] == "reported"
        assert rows["Temco Logistics"]["effective_date_end"] is None
        source_details = json.loads(rows["Temco Logistics"]["source_details"])
        assert source_details["date_evidence_rule"] == "tx_annual_workbook_dates_v1"
        details = source_details["tx_annual_workbook"]
        assert details["notice_date_role"] == "listed_warn_notice_date"
        assert details["receipt_date_role"] == "agency_received_date_not_notice_date"
        assert details["source_rows"][0]["row"] == 2
        assert rows["Autobahn Imports"]["notice_date_basis"] == "reported"
        assert rows["Autobahn Imports"]["effective_date_basis"] is None
        assert any(item["reason"] == "anomalous_source_effective_year_requires_review"
                   and item["source_fields"]["LayOff_Date"].startswith("1930-")
                   for item in exceptions)
        assert any(item["reason"] == "anomalous_raw_effective_year_outside_annual_corpus"
                   and item["source_fields"]["LayOff_Date"].startswith("2027-")
                   for item in exceptions)
    finally:
        conn.close()


def test_texas_date_annotation_holds_changed_rows_under_one_old_key(tmp_path):
    norm = normalize_file("tx", ROOT / "workdir/raw", load_registry()["tx"].source_url)
    versions = [row for row in norm.records
                if row["notice_date"] == "2020-04-10"
                and row["employer_name"] in {"Willie's Grill & Icehouse",
                                              "Willie's Grill & Icehouse."}]
    assert sorted(row["employees_affected"] for row in versions) == [32, 45]
    assert len({row["dedupe_key"] for row in versions}) == 1
    conn = db_mod.connect(tmp_path / "conflict.sqlite")
    try:
        db_mod.init_db(conn)
        ingest(conn, versions, observed_at="2026-09-22")
        exceptions = []
        report = apply_date_evidence(conn, ARTIFACTS, RAW, "2026-09-22", exceptions)
        assert report["reported_notice_dates"] == 0
        row = conn.execute("SELECT * FROM notices WHERE state='TX'").fetchone()
        assert row["notice_date_precision"] is None
        assert row["effective_date_precision"] is None
        assert any(item["reason"] == "same_key_version_raw_conflict" for item in exceptions)
    finally:
        conn.close()
