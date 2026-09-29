"""West Virginia WorkForce WV listing adapter (status: unverified).

Fixtures are a 2026-09-29 capture of the official listing page (trimmed to
its "YYYY WARN Listings" blocks) and the text lines of the agency summary PDF
with contact details removed.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from warnlive.fetch.custom import wv

FIXTURES = Path(__file__).parent / "fixtures" / "wv"


@pytest.fixture(scope="module")
def links():
    return wv.parse_listing((FIXTURES / "listing.html").read_text())


@pytest.fixture(scope="module")
def summary():
    pages = json.loads((FIXTURES / "summary_lines.json").read_text())["pages"]
    return wv.parse_summary_lines([[tuple(line) for line in page] for page in pages])


def _capture(tmp_path: Path, links, monkeypatch, summary) -> Path:
    """A fake capture dir: every linked PDF exists (bytes = its URL)."""
    root = tmp_path / "wv"
    for link in links:
        path = root / link["cache_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(link["source_url"].encode())
    pages = json.loads((FIXTURES / "summary_lines.json").read_text())["pages"]
    monkeypatch.setattr(
        wv, "summary_pdf_lines",
        lambda _path: [[tuple(line) for line in page] for page in pages],
    )
    return root


def test_listing_links_are_sectioned_pdfs(links):
    assert len(links) == 63
    assert {link["listing_section"] for link in links} == {
        f"{year} WARN Listings" for year in range(2021, 2027)
    }
    assert all(link["source_url"].startswith("https://workforcewv.org/wp-content/uploads/")
               for link in links)
    # Two labels are listed twice under different upload URLs.
    titles = [link["listing_title"] for link in links]
    assert titles.count("Cygnus Home Services -Yelloh WARN 7-16-24") == 2


def test_summary_entries_keep_labeled_dates_and_bold_changes(summary):
    window, entries = summary
    assert window == (date(2022, 1, 1), date(2025, 1, 3))
    assert len(entries) == 24
    by_company = {e["company"]: e for e in entries}
    elk = by_company["Elk Run Coal Company"]
    assert (elk["notice_date"], elk["projected_date"], elk["county"], elk["affected"]) == (
        "10/17/24", "12/6/24", "Boone", "66")
    # A block split across a page break keeps its county list and site counts.
    cygnus = next(e for e in entries if e["notice_date"] == "9/23/24")
    assert cygnus["county"] == "Raleigh, Wood, Harrison, Jefferson, and Mineral"
    assert cygnus["affected"].startswith("Total 54; Beaver, WV 9")
    assert "Contact" not in json.dumps(entries)
    watsonville = by_company["Watsonville Community Hospital"]
    assert watsonville["entry_note"] == "Postponement of 11/26/21notice"
    assert watsonville["bold_fields"] == "notice_date;projected_date"
    carter = [e for e in entries if e["company"] == "Carter Roag Coal Company"]
    assert [(e["notice_date"], e["entry_note"]) for e in carter] == [
        ("6/19/23", "Update"), ("5/12/23", "")]
    assert not any(e["bold_fields"] for e in entries if e is not watsonville)


def test_label_employer_and_title_date_roles():
    assert wv.label_employer("Mettiki_Supplemental_WARN_State_Notice_04_1_2026") == "Mettiki"
    assert wv.label_employer("WARN Notice State – West Virginia Conduent") == "Conduent"
    assert wv.label_employer("Carter Roag Coal Company WARN5-12-23") == "Carter Roag Coal Company"
    assert wv.label_employer("Beckley Mechanic Shop r1 WARN 6-4-25") == "Beckley Mechanic Shop"
    assert wv.label_employer("Greenbrier Minerals July WARN") == "Greenbrier Minerals"
    doc = wv.listing_document({
        "listing_title": "Mylan Pharmaceuticals WARN 5-25-21 Update",
        "listing_section": "2021 WARN Listings",
        "source_url": "https://workforcewv.org/wp-content/uploads/2025/02/x.pdf",
    })
    assert doc["listing_title_date"] == "5-25-21"
    assert doc["document_role"] == "amendment"
    assert "notice_date" not in doc
    assert doc["source_identity"] == "WV:/wp-content/uploads/2025/02/x.pdf"


def test_window_routing_holds_possible_summary_duplicates(tmp_path, links, summary, monkeypatch):
    root = _capture(tmp_path, links, monkeypatch, summary)
    rows, review = wv.build_rows(links, root)
    kinds = [row["record_kind"] for row in rows]
    assert kinds.count("summary_entry") == 24
    assert kinds.count("listing_document") == 34
    assert len(review) == 28
    # Every link except the summary PDF is accounted for exactly once.
    docs = [r["source_url"] for r in rows if r["record_kind"] == "listing_document"]
    held = [r["source_url"] for r in review]
    assert sorted(docs + held) == sorted(
        link["source_url"] for link in links if "WV-WARN-Notices" not in link["source_url"])
    reasons = {r["listing_title"]: r["review_reason"] for r in review}
    # Kroger has no summary entry but its label falls in the window: held, not dropped.
    assert reasons["Kroger Gassaway 10-28-22"] == "label_date_inside_summary_window"
    assert reasons["WARN VIMO INC"] == "undated_label_in_summary_window_year"
    assert reasons["HealthHelp WARN 12323"] == "undated_label_in_summary_window_year"
    admitted = {r["listing_title"] for r in rows if r["record_kind"] == "listing_document"}
    assert {"JeniusBank WARN 6-4-26", "WARN Notice State – West Virginia Conduent",
            "Greenbrier Minerals July WARN", "Watsonville Community Hospital WARN 11-26-21",
            } <= admitted
    identities = [r["source_identity"] for r in rows]
    assert len(set(identities)) == len(identities)


def test_normalized_dates_keep_roles(tmp_path, links, summary, monkeypatch):
    from warnlive.normalize.custom.wv import projected_start
    from warnlive.normalize.engine import normalize_file

    assert projected_start("7/12/23 to 7/26/23") == "7/12/23"
    assert projected_start("7/26/23-12/31/23") == "7/26/23"
    assert projected_start("8/4/23 and 8/18/23") == ""
    assert projected_start("7/12/23-7/26/23 8/19/23-9/1/23") == ""

    root = _capture(tmp_path, links, monkeypatch, summary)
    rows, review = wv.build_rows(links, root)
    wv._write(tmp_path / "raw", rows, review)
    result = normalize_file("wv", tmp_path / "raw", wv.PAGE_URL, observed_at="2026-09-29")
    assert result.raw_rows == 58 and result.failed_rows == 0
    by_name = {}
    for rec in result.records:
        by_name.setdefault(rec["employer_name"], []).append(rec)
    elk = by_name["Elk Run Coal Company"][0]
    assert (elk["notice_date"], elk["effective_date"], elk["employees_affected"]) == (
        "2024-10-17", "2024-12-06", 66)
    cygnus = by_name["Cygnus Home Servies, LLC, d/b/a Yelloh"]
    assert sorted(r["employees_affected"] for r in cygnus) == [1, 54]
    watsonville = {r["notice_date"]: r["is_amendment"] for r in by_name["Watsonville Community Hospital"]}
    assert watsonville == {"2022-01-25": 1, None: 0}  # summary postponement; 2021 letter
    jenius = by_name["JeniusBank"][0]
    # The label date 6-4-26 has no stated role; it is not a notice date.
    assert jenius["notice_date"] is None and jenius["effective_date"] is None
    assert json.loads(jenius["raw_extra"])["listing_title_date"] == "6-4-26"
    alderson = by_name["Alderson Broaddus University"][0]
    assert alderson["effective_date"] is None  # "and" list: phases, not one date
    with (tmp_path / "raw" / "wv.listing_review.csv").open() as fh:
        assert len(list(csv.DictReader(fh))) == 28


def test_cached_summary_pdf_matches_fixture():
    """Replay the real PDF when a capture is present (skipped otherwise)."""
    pdf = Path("workdir/cache/wv/pdf/2025/01/WV-WARN-Notices-1-1-22-to-1-3-25.pdf")
    if not pdf.is_file():
        pytest.skip("WV summary PDF capture is not available")
    window, entries = wv.parse_summary_lines(wv.summary_pdf_lines(pdf))
    assert window == (date(2022, 1, 1), date(2025, 1, 3))
    assert len(entries) == 24


def test_undated_wv_documents_for_one_employer_keep_distinct_keys():
    from warnlive.normalize.engine import _dedupe_key

    base = {"state": "WV", "employer_name": "Greenbrier Minerals",
            "notice_date": None, "location": None, "source_details": "{}"}
    first = _dedupe_key({**base, "source_identity": "WV:/wp-content/uploads/2025/04/a.pdf"})
    second = _dedupe_key({**base, "source_identity": "WV:/wp-content/uploads/2026/02/b.pdf"})
    assert first != second
