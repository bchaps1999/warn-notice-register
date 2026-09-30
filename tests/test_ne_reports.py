"""Nebraska: WARN report vs layoff/closure report, and Location keys.

Fixtures are unmodified-row excerpts of NDOL year reports retrieved
2026-09-30 (UTC):
  https://dol.nebraska.gov/LayoffServices/WARNReportData/?year=2015
  https://dol.nebraska.gov/LayoffServices/LayoffAndClosureReportData/?year=2015
  https://dol.nebraska.gov/LayoffServices/LayoffAndClosureReportData/?year=2010 (whole page)
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from warnlive.backfill import state_archives
from warnlive.fetch.patches import ne as ne_patch
from warnlive.migrate import ne_transition
from warnlive.normalize.custom import ne
from warnlive.normalize.engine import normalize_file

FIXTURES = Path(__file__).parent / "fixtures" / "ne_reports"
PAGE_FIXTURE = (
    Path(__file__).parent / "fixtures" / "ne_dol" / "active-20250323171358-excerpt.html"
)
PAGES = {
    state_archives.NE_REPORT_URLS["warn_report"].format(year=2015):
        FIXTURES / "warn_report-2015-excerpt.html",
    state_archives.NE_REPORT_URLS["layoff_closure_report"].format(year=2015):
        FIXTURES / "layoff_closure_report-2015-excerpt.html",
}


@pytest.fixture
def one_year(monkeypatch):
    def download(url: str, dest: Path) -> bytes:
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(PAGES[url].read_bytes())
        return dest.read_bytes()

    monkeypatch.setattr(state_archives, "NE_REPORT_YEARS", range(2015, 2016))
    monkeypatch.setattr(state_archives, "_download", download)


def _write(tmp_path: Path, rows: list[dict]) -> Path:
    with open(tmp_path / "ne.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=state_archives.NE_RAW_FIELDS)
        writer.writeheader()
        writer.writerows({name: row.get(name, "") for name in state_archives.NE_RAW_FIELDS}
                         for row in rows)
    return tmp_path


def test_year_reports_are_read_like_upstream():
    warn = state_archives.parse_ne_report(
        (FIXTURES / "warn_report-2015-excerpt.html").read_text(), "warn_report", 2015)
    assert [(r["Company"], r["City"], r["Location"], r["page_row"]) for r in warn] == [
        ("IAC Acoustics", "Lincoln", "Lincoln", 1),
        ("Skag-Way Discount Department Stores, Inc.", "Grand Island",
         "Grand Island (Locust Street)", 2),
        ("Skag-Way Discount Department Stores, Inc.", "Grand Island",
         "Grand Island (State Street)", 3),
    ]
    assert "Type" not in warn[0]
    layoff = state_archives.parse_ne_report(
        (FIXTURES / "layoff_closure_report-2015-excerpt.html").read_text(),
        "layoff_closure_report", 2015)
    assert [(r["Company"], r["Type"], r["Jobs Affected"]) for r in layoff] == [
        ("H & H Motors Omaha dba H & H KIA", "Closure", "53"),
        ("IAC Acoustics", "Closure", "160"),
    ]
    empty = state_archives.parse_ne_report(
        (FIXTURES / "layoff_closure_report-2010.html").read_text(),
        "layoff_closure_report", 2010)
    assert empty == []


def test_wrong_report_or_year_is_refused():
    html = (FIXTURES / "warn_report-2015-excerpt.html").read_text()
    with pytest.raises(ValueError, match="title"):
        state_archives.parse_ne_report(html, "layoff_closure_report", 2015)
    with pytest.raises(ValueError, match="not for 2016"):
        state_archives.parse_ne_report(html, "warn_report", 2016)


def test_report_rows_are_tagged_and_matches_recorded(tmp_path, one_year):
    rows, counts = state_archives.ne_report_rows(tmp_path)
    assert [(r["source_report"], r["Company"], r["ndol_matched_warn_row"]) for r in rows] == [
        ("warn_report", "IAC Acoustics", ""),
        ("warn_report", "Skag-Way Discount Department Stores, Inc.", ""),
        ("warn_report", "Skag-Way Discount Department Stores, Inc.", ""),
        ("layoff_closure_report", "H & H Motors Omaha dba H & H KIA", ""),
        # Same employer and date as WARN report row 1.
        ("layoff_closure_report", "IAC Acoustics", "warn_report:2015:1"),
    ]
    assert rows[0]["Type"] == "" and rows[3]["Type"] == "Closure"
    assert rows[4]["ndol_source_page"].endswith("LayoffAndClosureReportData/?year=2015")
    assert counts["2015"]["warn_report"] == 3
    assert counts["2015"]["layoff_closure_report"] == 2
    assert counts["2015"]["layoff_closure_report_matching_warn_row"] == 1
    meta = json.loads((tmp_path / "archives/ne/warn_report-2015.html.json").read_text())
    assert meta["sha256"] == hashlib.sha256(PAGES[meta["url"]].read_bytes()).hexdigest()
    assert meta["retrieved_at"]


def test_cached_report_must_match_its_recorded_hash(tmp_path, one_year):
    state_archives.ne_report_rows(tmp_path)
    cached = tmp_path / "archives/ne/layoff_closure_report-2015.html"
    cached.write_bytes(cached.read_bytes().replace(b"KIA", b"KIE"))
    with pytest.raises(ValueError, match="sha256"):
        state_archives.ne_report_rows(tmp_path)


def test_failed_report_fetch_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(state_archives, "_download", lambda url, dest: None)
    with pytest.raises(ValueError, match="fetch failed"):
        state_archives.ne_report_capture("warn_report", 2015, tmp_path)


def test_location_is_city_else_the_filed_location(tmp_path):
    _write(tmp_path, [
        {"Date": "3/23/2015", "Company": "Skag-Way", "Jobs Affected": "92",
         "City": "Grand Island", "Location": "Grand Island (Locust Street)"},
        {"Date": "01/08/2024", "Company": "Cardinal Health", "Jobs Affected": "13",
         "City": "", "Location": "Norfolk"},
    ])
    result = normalize_file("ne", tmp_path, "x", observed_at="2026-09-30")
    assert [r["location"] for r in result.records] == ["Grand Island", "Norfolk"]


def test_hold_and_details_follow_the_tag():
    layoff = {"source_report": "layoff_closure_report", "ndol_matched_warn_row": "warn_report:2015:1",
              "ndol_source_page": "p", "ndol_page_row": "19", "City": "Lincoln"}
    assert ne.hold_reason(layoff) == "ne_layoff_closure_report_not_warn"
    assert ne.hold_reason({"source_report": "warn_report"}) is None
    assert ne.hold_reason({}) is None  # an untagged upstream row is not classified
    assert ne.source_details(layoff) == {
        "source_report": "layoff_closure_report", "source_page": "p",
        "source_page_row": "19", "matched_warn_report_row": "warn_report:2015:1",
    }
    assert ne.source_details({"source_report": "warn_report", "Location": "Omaha"}) == {
        "source_report": "warn_report", "location_source_field": "Location",
    }
    assert ne.source_details({}) == {}
    with pytest.raises(ValueError):
        ne.source_details({"source_report": "other"})


def test_layoff_closure_rows_are_held_with_their_report(tmp_path, one_year):
    """Needs the NE hooks in normalize.details and normalize.nonnotice."""
    rows, _ = state_archives.ne_report_rows(tmp_path / "cache")
    _write(tmp_path, rows)
    result = normalize_file("ne", tmp_path, "x", observed_at="2026-09-30")
    assert [r["employer_name"] for r in result.records] == [
        "IAC Acoustics", "Skag-Way Discount Department Stores, Inc.",
        "Skag-Way Discount Department Stores, Inc.",
    ]
    assert {json.loads(r["source_details"])["source_report"] for r in result.records} == {
        "warn_report"}
    assert result.held_rows == 2 and result.failure_rate == 0
    assert {f["hold_reason"] for f in result.failures} == {"ne_layoff_closure_report_not_warn"}
    matched = [json.loads(f["raw_extra"]) for f in result.failures]
    assert [raw["ndol_matched_warn_row"] for raw in matched] == ["", "warn_report:2015:1"]


def test_collector_writes_page_and_report_rows(tmp_path, one_year, monkeypatch):
    monkeypatch.setattr(ne_patch, "_fetch_page", lambda cache: (PAGE_FIXTURE.read_bytes(), {}))
    path = ne_patch.scrape(tmp_path / "data", tmp_path / "cache")
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == state_archives.NE_RAW_FIELDS
        rows = list(reader)
    page = [r for r in rows if r["ndol_source_page"] == ne_patch.PAGE_URL]
    assert len(page) == 6 and len(rows) == 6 + 5
    assert page[0]["Company"] == "ITC Federal" and page[0]["source_report"] == "warn_report"
    assert page[0]["City"] == "" and page[0]["Location"]
    assert page[0]["ndol_notice_link"].startswith("https://dol.nebraska.gov/webdocs/")


def test_transition_maps_published_keys(tmp_path, one_year):
    rows, _ = state_archives.ne_report_rows(tmp_path / "cache")
    rows.append({"Date": "01/08/2024", "Company": "Cardinal Health", "Jobs Affected": "13",
                 "City": "", "Location": "Norfolk", "source_report": "warn_report",
                 "ndol_source_page": ne_patch.PAGE_URL, "ndol_page_row": "28"})
    raw = _write(tmp_path, rows) / "ne.csv"
    records = ne_transition.source_records(raw)
    old = {(r["employer_name"], r["report"]): r["old_key"] for r in records}
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notices (id INTEGER PRIMARY KEY, dedupe_key TEXT, state TEXT, "
                 "employer_name TEXT, location TEXT, notice_date TEXT)")
    published = [
        (1, old[("IAC Acoustics", "warn_report")], "IAC Acoustics", "Lincoln", "2015-11-17"),
        (2, old[("Skag-Way Discount Department Stores, Inc.", "warn_report")],
         "Skag-Way Discount Department Stores, Inc.", "Grand Island", "2015-03-23"),
        (3, old[("H & H Motors Omaha dba H & H KIA", "layoff_closure_report")],
         "H & H Motors Omaha dba H & H KIA", "Omaha", "2015-12-31"),
        (4, old[("Cardinal Health", "warn_report")], "Cardinal Health", None, "2024-01-08"),
    ]
    conn.executemany("INSERT INTO notices VALUES (?,?,'NE',?,?,?)", published)
    conn.commit()
    conn.close()
    out, summary = ne_transition.transition(db, raw)
    actions = {row["published_notice_id"]: row for row in out}
    assert actions[1]["action"] == "keep"
    assert "layoff/closure rows with this key are held" in actions[1]["evidence"]
    assert actions[1]["matched_warn_report_rows"] == "warn_report:2015:1"
    assert actions[2]["action"] == "keep"  # same City; sites collide as before
    assert actions[3]["action"] == "retire" and actions[3]["new_dedupe_key"] == ""
    assert actions[4]["action"] == "rekey" and actions[4]["new_location"] == "Norfolk"
    assert actions[4]["new_dedupe_key"] != actions[4]["old_dedupe_key"]
    assert summary["actions"] == {"keep": 2, "retire": 1, "rekey": 1}
