"""Kentucky agency archive (1998-2016 tracking form, 2017-2025 reports)."""

import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.ky_source import (
    ARCHIVE_SHA256, REPORT_2025, TRACKING, project_archive, read_archive,
)

ARCHIVE = Path(__file__).resolve().parents[1] / "data/source_snapshots/ky/kcc-2026-09-30"
# The v1.2 build's agency/ky notices received in December 2025, and the one
# document URL that 2026 report links from notice 2820.
V12_IDS = {"2733", "2734", "2735", "2737"}
V12_URL = ("https://kydev.my.salesforce.com/sfc/p/t00000004X3h/a/eq00000CLJC1/"
           "DbyYp68sVwKoN.GQkRf5EyzUntPUC3BOkVG9L_FcJUc")


def _project():
    return project_archive(ARCHIVE, V12_IDS, {V12_URL})


def test_every_archive_row_is_admitted_or_held_with_a_reason():
    rows = read_archive(ARCHIVE)
    records, held, report = _project()
    assert len(rows) == 1227 == len(records) + len(held)
    assert report["rows_by_file"] == {
        "tracking-form-1998-2016.xlsx": 799, "warn-report-2017-2025-02.xlsx": 368,
        "warn-report-2025.csv": 60}
    assert (report["admitted"], report["held"]) == (1150, 77)
    assert report["hold_reasons"] == {
        "agency_comment_rescinded": 1, "already_represented_ky_id": 4,
        "amendment_row_parent_unresolved": 33, "duplicate_row_in_source": 6,
        "ky_tracking_source_not_warn": 12,
        "listed_in_ky_2025_report_with_notice_number": 11,
        "source_explicitly_out_of_state": 10,
    }
    assert len({row["source_row"] for row in rows}) == len(rows)
    assert {item["source_row"] for item in held}.isdisjoint(
        json.loads(rec["source_details"])["source_row"] for rec in records)
    assert all(item["raw_extra"] and item["source_row_sha256"] for item in held)


def test_identities_prefer_agency_number_then_document_then_row():
    records, held, report = _project()
    assert report["admitted_by_identity_basis"] == {
        "agency_notice_document_url": 340, "agency_notice_number": 55,
        "agency_workbook_row": 755}
    assert len({rec["source_identity"] for rec in records}) == len(records)
    assert len({rec["dedupe_key"] for rec in records}) == len(records)
    by_id = {rec["source_identity"]: rec for rec in records}
    # Already-admitted 2026-report numbers are held, never admitted again.
    assert not V12_IDS & {rec["source_notice_id"] for rec in records}
    assert {item["source_notice_id"] for item in held
            if item["reason"] == "already_represented_ky_id"} == V12_IDS
    # Notice 2657 shares a document URL with 2026 notice 2820 but keeps its number.
    assert by_id["KY:2657"]["employer_name"] == "Levi Strauss & Co"
    # The workbook's 2025 sheet repeats CSV rows (same notice URL): held.
    listed = [item for item in held
              if item["reason"] == "listed_in_ky_2025_report_with_notice_number"]
    assert all(item["related_source_row"].startswith(f"agency/ky_archive/{REPORT_2025}")
               for item in listed)


def test_dates_keep_agency_roles_and_counts_are_stored_values():
    records, _, _ = _project()
    assert all(rec["notice_date"] is None for rec in records)
    by_id = {rec["source_identity"]: rec for rec in records}
    moveret = by_id["KY:2738"]
    details = json.loads(moveret["source_details"])
    assert (moveret["effective_date"], details["agency_received_date"]) == ("2025-12-31", "2025-12-19")
    assert details["date_roles"] == {"Date Received": "agency_receipt",
                                     "Projected Date": "projected_action"}
    # A blank count in the 2025 CSV blanks the field and keeps the notice.
    assert by_id["KY:2611"]["employees_affected"] is None
    # A count typed into a date-formatted cell keeps its stored number.
    mine = by_id["KY:document:t00000004LuH"]
    assert mine["employees_affected"] == 99
    assert json.loads(mine["source_details"])["stored_cell_numbers"] == {"Employees": 99}
    # An explicit projected range keeps both ends.
    donnelly = by_id["KY:archive:tracking:WARN 2001:r7"]
    assert (donnelly["effective_date"], donnelly["effective_date_end"]) == ("2002-01-03", "2002-04-15")
    assert donnelly["effective_date_end_basis"] == "reported"


def test_amendments_rescissions_and_non_warn_rows_are_held():
    _, held, _ = _project()
    reasons = {item["source_row"].split(":sheet:")[-1]: item["reason"] for item in held
               if ":sheet:" in item["source_row"]}
    assert reasons["WARN 1999:row:13"] == "agency_comment_rescinded"
    assert reasons["WARN 2010:row:17"] == "amendment_row_parent_unresolved"
    sources = {json.loads(item["raw_extra"])["cells"].get("Source") for item in held
               if item["reason"] == "ky_tracking_source_not_warn"}
    assert "Media" in sources and not any("WARN" in (s or "").upper() for s in sources)


def test_tracking_sheet_reentries_are_held_with_their_differences():
    _, held, _ = _project()
    by_row = {item["source_row"].split(":sheet:")[-1]: item for item in held
              if ":sheet:" in item["source_row"]}
    expected = {
        # Same listing retyped: "See WARN" vs "See Worker Layoff Report".
        "WARN 2001:row:12": ("WARN 2001:row:9", "Affected Occupations"),
        # Atlantis Plastics: industry code corrected on the second entry.
        "WARN 2008:row:29": ("WARN 2008:row:28", "NAICS/SIC"),
        # Panasonic: "09/30/2008 - 03/31/2009" vs a 2008-09-30 date cell.
        "WARN 2008:row:37": ("WARN 2008:row:34", "Projected Dates"),
        "WARN 2009:row:60": ("WARN 2009:row:59", "Workforce Area"),
    }
    for row, (earlier, column) in expected.items():
        item = by_row[row]
        assert item["reason"] == "duplicate_row_in_source"
        assert item["related_source_row"].endswith(f":sheet:{earlier}")
        assert column in item["differing_columns"]
    # One-worker listings naming different occupations stay separate notices.
    for row in ("WARN 2009:row:33", "WARN 2009:row:35",
                "WARN 2009:row:54", "WARN 2009:row:55"):
        assert row not in by_row


def test_archive_checksum_and_manifest_drift_fail_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    path = tmp_path / TRACKING
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_archive(tmp_path)
    shutil.copy2(ARCHIVE / TRACKING, path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["artifacts"][0]["data_rows"] += 1
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest changed"):
        read_archive(tmp_path)
    assert set(ARCHIVE_SHA256) == {item["file"] for item in json.loads(
        (ARCHIVE / "manifest.json").read_text())["artifacts"]}
