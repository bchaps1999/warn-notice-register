"""Nevada 2021: the scanned list is admitted from its recorded transcription."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from warnlive.fetch.custom import nv as nv_fetch
from warnlive.normalize.custom import nv
from warnlive.normalize.engine import normalize_file

EVIDENCE = (Path(__file__).resolve().parents[1] / "data" / "source_snapshots"
            / "2026-09-30-nv-2021-transcription")
SCAN_SHA = "9f3dadcdcc42903fa1e3f31ffda793ca1b06e78d2e824bcf36ccb1a4b81daaa5"


def test_evidence_manifest_matches_its_files():
    manifest = json.loads((EVIDENCE / "manifest.json").read_text())
    for item in manifest["files"]:
        data = (EVIDENCE / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["path"]
    assert hashlib.sha256((EVIDENCE / "WARN_2021.pdf").read_bytes()).hexdigest() == SCAN_SHA


def test_scanned_pdf_has_no_text_rows_and_a_recorded_transcription():
    assert nv_fetch.parse_pdf(EVIDENCE / "WARN_2021.pdf") == []
    assert SCAN_SHA in nv_fetch.TRANSCRIPTIONS
    assert nv_fetch.transcribed_rows("0" * 64) == []


def test_transcribed_rows_use_the_raw_columns():
    rows = nv_fetch.transcribed_rows(SCAN_SHA)
    assert len(rows) == 20
    assert set(rows[0]) == set(nv_fetch.COLUMNS + nv_fetch.TRANSCRIPTION_COLUMNS)
    assert rows[6]["Employer"] == "A & B Precision Metals, Inc."
    assert rows[6]["transcription_source"].endswith("nv-2021-transcription.csv#r07")
    assert {row["Notification"] for row in rows} == {"WARN"}
    assert {row["transcription_basis"] for row in rows} == {"ocr+manual"}


def test_transcription_details():
    assert nv.transcription_details({"transcription_basis": ""}) == {}
    assert nv.transcription_details(
        {"transcription_basis": "manual", "transcription_source": "x#r01"}
    ) == {"transcription": {"basis": "manual", "source": "x#r01",
                            "rule": "nv_scanned_list_transcription_v1"}}


def test_transcribed_rows_normalize_with_their_evidence(tmp_path):
    """Needs the NV transcription hook in normalize.details."""
    with open(tmp_path / "nv.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=nv_fetch.COLUMNS + nv_fetch.TRANSCRIPTION_COLUMNS)
        writer.writeheader()
        writer.writerows(nv_fetch.transcribed_rows(SCAN_SHA))
    result = normalize_file("nv", tmp_path, "https://detr.nv.gov/Page/WARN",
                            observed_at="2026-09-30")
    assert result.failed_rows == 0 and len(result.records) == 20
    assert len({rec["dedupe_key"] for rec in result.records}) == 20
    first = result.records[0]
    details = json.loads(first["source_details"])
    assert details["agency_received_date"] == "2021-01-12"
    assert details["transcription"]["source"].endswith("#r01")
    assert (first["employer_name"], first["location"], first["effective_date"],
            first["employees_affected"], first["layoff_type"], first["notice_date"]) == (
        "Food Source", "Reno, Washoe", "2021-03-31", 33, "closure", None)
