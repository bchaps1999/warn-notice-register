"""Tennessee Wayback archive: 2012-2017 month report and 2018-2021 page captures."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.tn_source import (
    REPORT_PDF, _canonical, project_archive, read_archive, read_artifacts,
)

ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/tn"
ARCHIVE = ROOT / "wayback-2026-09-30"


def _known() -> set[str]:
    return {row["fields"].get("Notice/Type", "") for row in read_artifacts(ROOT)[0]}


@lru_cache(maxsize=1)
def _projected():
    return project_archive(ARCHIVE, _known())


def test_report_and_page_rows_are_all_accounted_for():
    data = read_archive(ARCHIVE)
    assert (len(data["report"]), len(data["pages"])) == (511, 667)
    records, held, report = _projected()
    assert len(records) + len(held) == 1178
    assert report["hold_reasons"] == {
        "already_represented_tn_warn_number": 26,
        "earlier_capture_text_of_warn_number": 256,
        "listed_on_reports_page_with_warn_number": 2,
        "warn_number_lists_different_events": 25,
    }
    assert (report["admitted"], report["admitted_report_rows"],
            report["admitted_warn_numbers"]) == (869, 509, 360)
    assert len({rec["dedupe_key"] for rec in records}) == len(records)
    assert all(item["raw_extra"] and item["source_row_sha256"] for item in held)


def test_warn_numbers_are_identities_and_known_numbers_are_not_readmitted():
    known = _known()
    records, held, _ = _projected()
    by_id = {rec["source_identity"]: rec for rec in records}
    # "2019008" and "20190008" are one agency number printed two ways.
    assert _canonical("2019008") == _canonical("20190008") == "2019:8"
    assert by_id["TN:warn:2019:8"]["employer_name"] == "Cumberland River Hospital"
    assert not {_canonical(x.lstrip("# ")) for x in known} & {
        rec["source_identity"].removeprefix("TN:warn:") for rec in records}
    # A number the agency reused for different employers is held in full.
    reused = {item["source_notice_id"] for item in held
              if item["reason"] == "warn_number_lists_different_events"}
    assert {"20190014", "202000062"} <= reused
    assert not any(_canonical(n) in {_canonical(x) for x in reused}
                   for n in (rec["source_notice_id"] for rec in records) if n)


def test_date_roles_stay_labeled():
    records, _, _ = _projected()
    by_id = {rec["source_identity"]: rec for rec in records}
    whelan = by_id["TN:warn:2018:7"]
    details = json.loads(whelan["source_details"])
    assert whelan["notice_date"] is None
    assert (details["agency_posted_date"], whelan["effective_date"]) == ("2018-02-22", "2018-04-01")
    assert details["date_roles"]["Date Notice Posted"] == "agency_posting"
    velsicol = by_id["TN:report-by-month:p1:r3"]
    details = json.loads(velsicol["source_details"])
    assert (velsicol["notice_date"], velsicol["effective_date"],
            details["agency_received_date"]) == ("2012-01-05", "2011-07-01", "2011-11-28")
    assert velsicol.get("notice_date_basis") is None
    assert details["date_roles"]["Notice Date"] == "agency_report_notice_date_role_unverified"
    assert (velsicol["layoff_type"], velsicol["is_temporary"]) == ("closure", 0)
    # The report's own month summary counts rows with a worker count; three
    # rows without one are kept with a blank count.
    assert sum(rec["employees_affected"] is None for rec in records) == 3


def test_report_page_overlap_is_held_not_duplicated():
    records, held, _ = _projected()
    overlap = [json.loads(item["raw_extra"])["Company"] for item in held
               if item["reason"] == "listed_on_reports_page_with_warn_number"]
    assert "Luxottica Retail North America Inc." in overlap
    assert any(rec["employer_name"] == "Luxottica Retail North America Inc."
               and rec["source_identity"].startswith("TN:warn:") for rec in records)


def test_tennessee_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    pdf = tmp_path / REPORT_PDF
    pdf.write_bytes(pdf.read_bytes()[:-1])
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_archive(tmp_path)
    shutil.copy2(ARCHIVE / REPORT_PDF, pdf)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["distinct_page_records"] += 1
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="distinct page record count"):
        read_archive(tmp_path)
