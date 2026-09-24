"""Hawaii adapter checks using the pinned transition source capture."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from warnlive.fetch.custom import hi
from warnlive.normalize.engine import normalize_file

PINNED = Path(__file__).resolve().parents[1] / "data/source_snapshots/hi/2026-09-23-transition"


def _html(name: str) -> str:
    return (PINNED / name).read_text()


def test_official_link_filter_and_relative_resolution():
    archive = _html("archive-index.html")
    current = _html("current-index.html")
    assert len(hi._archive_links(archive)) == 8
    assert len(hi._detail_links(current)) == 5
    assert hi._official("/wdc/2026-warn-notices/", hi.ARCHIVE_INDEX) == (
        "https://labor.hawaii.gov/wdc/2026-warn-notices/"
    )
    assert hi._official("https://other.example/wdc/2026-warn-notices/", hi.ARCHIVE_INDEX) is None
    assert all("email-protection" not in url for url in hi._archive_links(archive))


def test_pinned_archive_and_amentum_roles(tmp_path):
    archive = hi._archive_rows(_html("archive-2026.html"), "https://labor.hawaii.gov/wdc/2026-warn-notices/")
    assert len(archive) >= 20
    assert any("Rescinded" in row["status_text"] for row in archive)
    assert any("UPDATE" in row["status_text"] for row in archive)
    kauai = [row for row in archive if row["Company"] == "Kauai Coffee Company, LLC"]
    assert [row["archive_list_date"] for row in kauai] == ["2026-03-06", "2026-01-12"]
    assert all(row["Date"] == "" for row in kauai)
    assert all(row["document_role"] == "rescission_attachment" for row in kauai)
    assert all("WARN Rescinded" in row["status_text"] for row in kauai)
    amentum = hi._detail_row(_html("amentum.html"), "https://labor.hawaii.gov/wdd/warn-notices/warn-notice-amentum/")
    assert amentum["Date"] == ""
    assert amentum["Date Department Received WARN"] == "August 20, 2026"
    assert "Layoff Effective: October 31, 2026" in amentum["status_text"]
    assert "62" in amentum["count_text"]
    with (tmp_path / "hi.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=hi.BASE_COLUMNS + hi.EVIDENCE_COLUMNS)
        writer.writeheader()
        writer.writerow(amentum)
    result = normalize_file("hi", tmp_path, hi.ARCHIVE_INDEX)
    assert result.failed_rows == 0
    rec = result.records[0]
    detail = json.loads(rec["source_details"])
    assert rec["notice_date"] is None
    assert rec["effective_date"] == "2026-10-31"
    assert rec["employees_affected"] == 62
    assert rec["layoff_type"] == "mass_layoff"  # Amentum expressly labels Layoff Effective.
    assert detail["agency_received_date"] == "2026-08-20"
    assert detail["dates"][0]["role"] == "agency_received"
    assert detail["status_text"].startswith("Layoff Effective:")
    assert rec["source_identity"].startswith("HI:wdd:")


def test_legacy_five_columns_still_normalize(tmp_path):
    with (tmp_path / "hi.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=hi.BASE_COLUMNS)
        writer.writeheader()
        writer.writerow({"Company": "Legacy Co", "Date": "2025-07-01", "PDF url": "https://labor.hawaii.gov/legacy.pdf", "location": "Honolulu", "jobs": "10"})
    result = normalize_file("hi", tmp_path, hi.ARCHIVE_INDEX)
    assert result.failed_rows == 0
    assert result.records[0]["notice_date"] == "2025-07-01"
    assert result.records[0]["employees_affected"] == 10


def test_transition_headcount_is_not_affected_workers():
    from warnlive.normalize.custom.hi import _wdd_effective, _wdd_total

    transfer = {
        "Date of Closure or When Employees Will Be Affected": "Transition Completion Date: October 18, 2026",
        "Number of Affected Employees (Total)": "Total employees statewide: Approximately 117 Expected impact: all employees will continue employment",
    }
    assert _wdd_effective(transfer) == ""
    assert _wdd_total(transfer) == ""
    closing = {
        "Date of Closure or When Employees Will Be Affected": "Effective date of partial closing: On or after October 1, 2026",
        "Number of Affected Employees (Total)": "Total employees statewide: 212 Employees affected by partial closing: 3",
    }
    assert _wdd_effective(closing) == ""
    assert _wdd_total(closing) == "3"
    assert _wdd_effective({
        "Date of Closure or When Employees Will Be Affected":
        "Layoff Effective: On or after October 1, 2026",
    }) == ""
    assert _wdd_effective({
        "Date of Closure or When Employees Will Be Affected":
        "Layoff Effective: October 1, 2026 through November 1, 2026",
    }) == ""


def test_archive_listing_day_does_not_become_legal_notice(tmp_path):
    archive = hi._archive_rows(_html("archive-2026.html"),
                               "https://labor.hawaii.gov/wdc/2026-warn-notices/")
    with (tmp_path / "hi.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=hi.BASE_COLUMNS + hi.EVIDENCE_COLUMNS)
        writer.writeheader()
        writer.writerow(next(row for row in archive if row["Company"] == "Kauai Coffee Company, LLC"))
    result = normalize_file("hi", tmp_path, hi.ARCHIVE_INDEX)
    assert result.failed_rows == 0
    rec = result.records[0]
    assert rec["notice_date"] is None
    assert json.loads(rec["source_details"])["dates"][0]["role"] == "archive_list_date"


def test_missing_required_detail_is_atomic(tmp_path, monkeypatch):
    target = tmp_path / "hi.csv"
    target.write_text("previous capture\n")
    archive_urls = hi._archive_links(_html("archive-index.html"))
    detail_urls = hi._detail_links(_html("current-index.html"))

    def get(url):
        if url == hi.ARCHIVE_INDEX:
            return _html("archive-index.html")
        if url == hi.CURRENT_INDEX:
            return _html("current-index.html")
        if url in archive_urls:
            return _html("archive-2026.html")
        if url == detail_urls[0]:
            return _html("amentum.html")
        raise RuntimeError("detail unavailable")

    monkeypatch.setattr(hi, "_get", get)
    with pytest.raises(RuntimeError, match="detail unavailable"):
        hi.scrape(tmp_path, tmp_path)
    assert target.read_text() == "previous capture\n"
