"""Archived Iowa Workforce Development WARN logs, 2005-2020."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.ia_source import (
    extract, extract_archive, extract_historical, project_archive,
)

ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/ia"
ARCHIVE = ROOT / "wayback-2026-09-30"


@lru_cache(maxsize=1)
def _projected():
    return project_archive(ARCHIVE, extract(ROOT) + extract_historical(ROOT))


def test_every_archived_row_is_admitted_versioned_or_held():
    records, held, report = _projected()
    assert report["source_rows"] == 1054
    assert report["rows_by_file"] == {
        "WARN_20200420-2-20210309153239.xlsx": 352,
        "WARN_20180503-20210309152211.xlsx": 339,
        "warn_20150812-20161227190458.pdf": 363}
    assert (report["admitted"], report["amendment_versions"]) == (377, 61)
    assert len(records) + len(held) == 1054
    assert report["hold_reasons"] == {
        "amendment_parent_ambiguous": 37, "amendment_without_verified_parent": 64,
        "duplicate_agency_capture": 361, "listed_in_current_ia_logs": 132,
        "listing_differs_in_newer_archived_log": 5, "possible_revision_same_event": 6,
        "repeated_source_event_unresolved": 2, "site_phase_or_worker_allocation_unresolved": 4,
        "unresolved_source_layout_or_date": 5,
    }
    notices = [rec for rec in records if not rec["is_amendment"]]
    assert len({rec["dedupe_key"] for rec in notices}) == len(notices)
    # Every version belongs to an admitted archive notice.
    assert {rec["dedupe_key"] for rec in records if rec["is_amendment"]} <= {
        rec["dedupe_key"] for rec in notices}


def test_an_event_in_several_logs_is_one_observation():
    _, held, _ = _projected()
    by_row = {item["source_row"]: item for item in held}
    # The 2015 PDF and 2018 workbook cut or restyle names; the newest log is cited.
    buccaneer = by_row["agency/ia_archive/WARN_20180503-20210309152211.xlsx:WARN Log 7_12_17- 2:r142"]
    assert buccaneer["reason"] == "duplicate_agency_capture"
    assert buccaneer["related_source_row"] == (
        "agency/ia_archive/WARN_20200420-2-20210309153239.xlsx:WARN log for website:r16")
    current = by_row["agency/ia_archive/WARN_20200420-2-20210309153239.xlsx:WARN log for website:r220"]
    assert (current["reason"], current["related_source_row"]) == (
        "listed_in_current_ia_logs", "historical-2023.pdf:p1:r1")


def test_a_retyped_name_does_not_hide_a_repeated_event():
    records, held, _ = _projected()
    by_row = {item["source_row"].split("/")[-1]: item for item in held}
    newest = "WARN_20200420-2-20210309153239.xlsx:WARN log for website"
    middle = "WARN_20180503-20210309152211.xlsx:WARN Log 7_12_17- 2"
    pdf = "warn_20150812-20161227190458.pdf"
    # Same city, notice date, layoff date and count as a newer or current listing.
    expected = {
        f"{newest}:r222": ("listed_in_current_ia_logs", "historical-2023.pdf:p1:r3"),  # "eiber" = ACT, Inc.
        f"{middle}:r134": ("duplicate_agency_capture", f"agency/ia_archive/{newest}:r8"),  # IPSCO
        f"{middle}:r148": ("duplicate_agency_capture", f"agency/ia_archive/{newest}:r22"),  # "Resporces"
        f"{middle}:r278": ("duplicate_agency_capture", f"agency/ia_archive/{newest}:r152"),  # "Ic."
        f"{pdf}:p7:r20": ("duplicate_agency_capture", f"agency/ia_archive/{middle}:r58"),  # "Windposer"
    }
    for row, (reason, related) in expected.items():
        assert (by_row[row]["reason"], by_row[row]["related_source_row"]) == (reason, related)
    # No two admitted archive notices share city, both dates and a count.
    events = [(rec["location"], rec["notice_date"], rec["effective_date"], rec["employees_affected"])
              for rec in records if not rec["is_amendment"] and rec["employees_affected"] is not None]
    repeated = {event for event in events if events.count(event) > 1}
    shared = [json.loads(rec["source_details"]).get("filing_group") for rec in records
              if not rec["is_amendment"] and (rec["location"], rec["notice_date"], rec["effective_date"],
                                              rec["employees_affected"]) in repeated]
    # Only rows of one filing at distinct street addresses may repeat them.
    assert all(shared)


def test_amendments_use_the_event_log_rules():
    records, _, _ = _projected()
    apac = next(rec for rec in records if rec["is_amendment"])
    details = json.loads(apac["source_details"])
    assert (apac["employer_name"], apac["notice_date"], apac["employees_affected"]) == (
        "APAC Customer Services, Inc.", "2006-01-09", 292)
    assert details["amendment"]["workers_not_applied_reason"] == "amendment_count_meaning_unstated"
    assert details["identity_basis"] == "unique_agency_event_observation_not_filing_id"
    assert apac["notice_date_basis"] == "reported"


def test_iowa_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    name = manifest["artifacts"][0]["file"]
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        extract_archive(tmp_path)


def test_layoff_date_before_the_minimum_year_is_blanked():
    records, _, report = _projected()
    gleason = next(r for r in records if r["source_details"].count("warn_20150812-20161227190458.pdf:p2:r8"))
    assert gleason["notice_date"] == "2006-08-18" and gleason["effective_date"] is None
    assert "10/20/1969" in gleason["raw_extra"]
    assert [(b["field"], b["value"]) for b in report["implausible_dates_blanked"]] == [
        ("effective_date", "1969-10-20")]
