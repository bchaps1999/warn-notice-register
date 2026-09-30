"""Nebraska 2020-2022 from a pinned capture of NDOL's WARN page.

The fixture is an unmodified-row excerpt of
https://web.archive.org/web/20250323171358id_/https://dol.nebraska.gov/ReemploymentServices/LayoffServices/LayoffsAndDownsizingWARN
retrieved 2026-09-29.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from warnlive.backfill import state_archives

FIXTURE = (
    Path(__file__).parent / "fixtures" / "ne_dol" / "active-20250323171358-excerpt.html"
)


def _fake_download(content: bytes):
    def download(url: str, dest: Path) -> bytes:
        assert url == state_archives.NE_DOL_CAPTURE
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        return dest.read_bytes()
    return download


def test_page_rows_are_read_like_the_live_collector():
    rows = state_archives.parse_ne_dol_page(FIXTURE.read_text(), state_archives.NE_DOL_CAPTURE)
    assert [row["cells"][1] for row in rows] == [
        "ITC Federal", "Malco Products, SBC, Inc.", "PSSI Food Safety Solutions: JBS USA",
        "Hayneedle, Inc.", "Hayneedle, Inc.", "Packers Sanitation Services, Inc.",
    ]
    # td.text.strip(), exactly as warn-scraper ne.py reads the live page.
    assert rows[1]["cells"][0] == "12/19/2022\xa0\xa0\n\xa0 11/02/2022"
    assert rows[1]["link"] == (
        "https://dol.nebraska.gov/webdocs/getfile/7c7078e9-5189-4376-969e-dd9ba70a89e4"
    )
    assert rows[-1]["link"] == ""


def test_other_table_shapes_are_refused():
    with pytest.raises(ValueError, match="one WARN table"):
        state_archives.parse_ne_dol_page(
            "<table><tr><td>Date</td><td>Company</td></tr></table>", "x"
        )


def test_backfill_scopes_years_keys_sites_and_records_capture(
    tmp_path, monkeypatch,
):
    content = FIXTURE.read_bytes()
    monkeypatch.setattr(state_archives, "_download", _fake_download(content))
    records = state_archives.fetch_ne_dol(tmp_path)
    # ITC Federal (2024) is outside 2020-2022; the live collector owns it.
    # The two Hayneedle sites are keyed on their filed Location cells.
    assert [(r["employer_name"], r["notice_date"], r["location"]) for r in records] == [
        ("Malco Products, SBC, Inc.", "2022-11-02", "DeWitt"),
        ("PSSI Food Safety Solutions: JBS USA", "2022-12-13", "Grand Island"),
        ("Hayneedle, Inc.", "2020-01-23", "Chalco Valley Parkway - Omaha"),
        ("Hayneedle, Inc.", "2020-01-23", "West Dodge Road - Omaha"),
        ("Packers Sanitation Services, Inc.", "2020-01-18", "Lincoln - Smithfield"),
    ]
    assert len({r["dedupe_key"] for r in records}) == len(records)
    first = records[0]
    assert first["source_url"] == state_archives.NE_DOL_CAPTURE
    capture = json.loads(first["source_details"])["source_capture"]
    assert capture["sha256"] == hashlib.sha256(content).hexdigest()
    assert capture["capture_timestamp"] == "20250323171358"
    assert capture["original_url"] == state_archives.NE_DOL_ORIGINAL
    raw = json.loads(first["raw_extra"])
    assert raw["ndol_page_row"] == "2"
    assert raw["source_report"] == "warn_report"
    assert raw["ndol_source_page"] == state_archives.NE_DOL_CAPTURE
    assert raw["Date"] == "12/19/2022\xa0\xa0\n\xa0 11/02/2022"


def test_cached_capture_must_match_its_recorded_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(state_archives, "_download", _fake_download(FIXTURE.read_bytes()))
    state_archives.fetch_ne_dol(tmp_path)
    cached = tmp_path / "archives" / "ne" / "dol-warn-20250323171358.html"
    cached.write_bytes(cached.read_bytes().replace(b"Packers", b"Packerz"))
    with pytest.raises(ValueError, match="sha256"):
        state_archives.fetch_ne_dol(tmp_path)


def test_ne_is_registered_for_archive_backfill():
    assert state_archives.FETCHERS["NE"] is state_archives.fetch_ne_dol
