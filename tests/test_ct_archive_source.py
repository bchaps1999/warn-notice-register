"""Connecticut DOL annual WARN listing pages archived on the Wayback Machine."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.ct_archive_source import project, read_archive

ARCHIVE = Path(__file__).resolve().parents[1] / "data/source_snapshots/ct/wayback-2026-09-30"


@lru_cache(maxsize=1)
def _projected():
    return project(ARCHIVE)


def test_every_listing_row_is_admitted_or_held():
    records, held, report = _projected()
    assert report["source_rows"] == 939 == len(records) + len(held)
    assert report["hold_reasons"] == {
        "duplicate_row_in_source": 1, "official_source_continuation_row": 1,
        "official_source_rescinded": 1, "official_source_update_unresolved": 446,
    }
    assert (report["admitted"], report["admitted_missing_workers"],
            report["admitted_narrow_rows"], report["admitted_filing_group_rows"]) == (490, 52, 9, 10)
    assert len({rec["dedupe_key"] for rec in records}) == len(records)
    assert all(item["raw_extra"] and item["source_row_sha256"] for item in held)


def test_warn_and_receipt_dates_keep_their_roles():
    records, _, _ = _projected()
    by_id = {rec["source_identity"]: rec for rec in records}
    societe = by_id["CT:archive:2011:r1"]
    assert (societe["employer_name"], societe["notice_date"], societe["effective_date"],
            societe["effective_date_end"], societe["employees_affected"]) == (
        "Societe Generale Energy Co.", "2011-12-30", "2012-03-06", "2012-06-29", 129)
    details = json.loads(societe["source_details"])
    assert details["agency_received_date"] == "2012-01-03"
    assert details["date_roles"]["Rec'd"] == "agency_receipt"
    # "13 total: 2 CT residents" is not one count; the row is kept.
    cvs = by_id["CT:archive:2025:r1"]
    assert cvs["employees_affected"] is None and cvs["notice_date"] == "2025-01-17"
    # "Not Dated Rec'd 6/24/15": no WARN date, receipt date kept.
    klx = next(rec for rec in records if rec["employer_name"] == "KLX, Inc.")
    assert klx["notice_date"] is None
    assert json.loads(klx["source_details"])["agency_received_date"] == "2015-06-24"


def test_spanned_rows_share_a_filing_group_and_narrow_rows_keep_only_identity():
    records, held, _ = _projected()
    shaws = [rec for rec in records if rec["employer_name"] == "Shaw's Supermarkets, Inc."]
    assert len(shaws) == 8 and len({rec["location"] for rec in shaws}) == 8
    groups = {json.loads(rec["source_details"])["filing_group"]["id"] for rec in shaws}
    assert groups == {"CT:archive-filing-group:2010:77"}
    dollar = [rec for rec in records if rec["employer_name"] == "Dollar Express"]
    assert len(dollar) == 6
    assert all(rec["location"] is None and rec["employees_affected"] is None for rec in dollar)
    assert {json.loads(rec["source_details"])["cell_alignment"] for rec in dollar} == {
        "row_narrower_than_header_fields_after_company_unassigned"}
    reasons = {json.loads(item["raw_extra"])["cells"][1]: item["reason"] for item in held
               if len(json.loads(item["raw_extra"])["cells"]) > 1}
    assert reasons["Sodexo (Updated Notice - Notice rescinded)*"] == "official_source_rescinded"


def test_known_events_are_held():
    records, _, _ = _projected()
    societe = next(rec for rec in records if rec["source_identity"] == "CT:archive:2011:r1")
    again, held, _ = project(ARCHIVE, {("societegener", "2012-01-20")})
    assert societe["dedupe_key"] not in {rec["dedupe_key"] for rec in again}
    assert [item["reason"] for item in held].count("possible_overlap_with_current_ct_notice") == 1


def test_connecticut_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    name = manifest["artifacts"][0]["file"]
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_archive(tmp_path)
    shutil.copy2(ARCHIVE / name, tmp_path / name)
    manifest["artifacts"][0]["data_rows"] += 1
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="row count changed"):
        read_archive(tmp_path)
