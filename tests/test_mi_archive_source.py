"""Michigan WARN listings archived from the former michigan.gov pages."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.mi_archive_source import _employer_key, project, read_artifacts

ARCHIVE = Path(__file__).resolve().parents[1] / "data/source_snapshots/mi/wayback-2026-09-30"


@lru_cache(maxsize=1)
def _projected():
    return project(ARCHIVE, set())


def test_each_distinct_listing_is_admitted_or_held():
    rows, manifest = read_artifacts(ARCHIVE)
    assert len(rows) == manifest["distinct_listings"] == 938
    records, held, report = _projected()
    assert len(records) + len(held) == 938
    assert report["hold_reasons"] == {
        "amendment_listing_parent_unresolved": 12, "no_notice_document_link": 1,
        "other_capture_listing_of_document": 342,
    }
    assert (report["admitted"], report["admitted_missing_workers"],
            report["admitted_with_posted_day"]) == (583, 17, 561)
    # One notice per michigan.gov document id.
    assert len({rec["source_notice_id"] for rec in records}) == len(records)
    assert all(rec["source_identity"] == f"MI:document:{rec['source_notice_id']}" for rec in records)
    repeats = [item for item in held if item["reason"] == "other_capture_listing_of_document"]
    admitted_rows = {json.loads(rec["source_details"])["source_row"] for rec in records}
    amended_rows = {item["source_row"] for item in held
                    if item["reason"] == "amendment_listing_parent_unresolved"}
    # Each repeat points at its document's current listing, which is admitted
    # unless it is itself an update listing.
    assert all(item["related_source_row"] in admitted_rows | amended_rows for item in repeats)


def test_listing_day_is_a_posting_date_not_a_notice_date():
    records, _, _ = _projected()
    assert all(rec["notice_date"] is None and rec["effective_date"] is None for rec in records)
    by_id = {rec["source_identity"]: rec for rec in records}
    gm = by_id["MI:document:545891"]
    details = json.loads(gm["source_details"])
    assert (gm["employer_name"], gm["employees_affected"], gm["location"]) == (
        "GM Assembly Plant", 1192, "Detroit")
    assert details["agency_posted_date"] == "2016-12-19"
    assert details["date_roles"] == {"listing day under month heading": "agency_posting"}
    # A JSON-only listing has a year category and no day.
    progenity = json.loads(by_id["MI:document:727179"]["source_details"])
    assert "agency_posted_date" not in progenity and progenity["agency_listing_year"] == "2021"
    assert progenity["wayback_captures"] == ["20210625222531", "20220129040313"]


def test_amendments_and_non_notice_links_are_held():
    _, held, _ = _projected()
    titles = {json.loads(item["raw_extra"])["title"]: item["reason"] for item in held
              if item["reason"] != "other_capture_listing_of_document"}
    assert titles["Art Van Furniture"] == "amendment_listing_parent_unresolved"
    assert titles["More information on the WARN Act"] == "no_notice_document_link"


def test_current_listing_overlap_is_held():
    records, _, _ = _projected()
    gm = next(rec for rec in records if rec["source_notice_id"] == "545891")
    again, held, _ = project(ARCHIVE, {(_employer_key("GM Assembly Plant"), 1192)})
    assert gm["dedupe_key"] not in {rec["dedupe_key"] for rec in again}
    assert [item["source_notice_id"] for item in held
            if item["reason"] == "possible_overlap_with_current_mi_listing"] == ["545891"]


def test_michigan_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    name = "year-2016-20200614041252.html"
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
    shutil.copy2(ARCHIVE / name, tmp_path / name)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["distinct_listings"] -= 1
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="distinct listing count"):
        read_artifacts(tmp_path)
