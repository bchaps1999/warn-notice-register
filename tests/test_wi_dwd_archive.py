"""Wisconsin 2016-2019 from DWD's static year pages (state_archives).

Fixtures are unmodified month blocks of the official pages, retrieved
2026-09-29 from https://dwd.wisconsin.gov/dislocatedworker/warn/YYYY/default.htm.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from warnlive.backfill import state_archives

FIXTURES = Path(__file__).parent / "fixtures" / "wi_dwd"


def _page(year: int) -> str:
    return (FIXTURES / f"{year}-excerpt.htm").read_text()


def _fake_download(year: int):
    def download(url: str, dest: Path) -> bytes:
        assert url == state_archives.WI_DWD_URL.format(year=year)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_page(year).encode())
        return dest.read_bytes()
    return download


def test_page_rows_keep_live_columns_revisions_and_highlights():
    url = state_archives.WI_DWD_URL.format(year=2019)
    rows, pointers = state_archives.parse_wi_dwd_page(_page(2019), url, 2019)
    assert pointers == 0
    assert [r["Company"] for r in rows] == [
        "Regal Beloit America Inc.",
        *(f"Regal Beloit America Inc. - Revision {n}" for n in range(1, 5)),
        "Alorica, Inc.",
    ]
    first = rows[0]
    # The page leaves Layoff Begin Date unterminated; the cell must not
    # absorb the following NAICS/County cells.
    assert first["Layoff Begin Date"] == "01/31/2020"
    assert first["NAICS Description"] == "Speed Changer Drive & Gear Mfg"
    assert (first["Notice Received"], first["Affected Workers"], first["County"]) == (
        "12/2/2019", "59", "Rock")
    assert first["dwd_row_id"] == "2019120201"
    assert first["dwd_notice_pdf"] == (
        "https://dwd.wisconsin.gov/dislocatedworker/warn/2019/2019120201.pdf")
    assert first["dwd_month_heading"] == "December 2019"
    assert first["dwd_updated_cells"] == ""
    assert rows[1]["dwd_updated_cells"] == "row"
    assert rows[1]["dwd_notice_pdf"].endswith("/dislocatedworker/warn/2020/2020011101.pdf")


def test_update_pointer_tables_are_counted_not_rows():
    url = state_archives.WI_DWD_URL.format(year=2018)
    rows, pointers = state_archives.parse_wi_dwd_page(_page(2018), url, 2018)
    assert pointers == 1
    assert all(r["Notice Received"] for r in rows)
    assert "Mayline (DBA Safco Products Co.)" not in {r["Company"] for r in rows}


def test_fetch_normalizes_with_live_wi_roles_and_records_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(state_archives, "WI_DWD_YEARS", [2019])
    monkeypatch.setattr(state_archives, "_download", _fake_download(2019))
    records = state_archives.fetch_wi_dwd(tmp_path)
    assert len(records) == 6
    meta_path = tmp_path / "archives" / "wi" / "dwd-2019.htm.json"
    meta = json.loads(meta_path.read_text())
    assert meta["url"] == state_archives.WI_DWD_URL.format(year=2019)
    assert meta["retrieved_at"] and len(meta["sha256"]) == 64
    assert (tmp_path / "archives" / "wi" / "dwd-2019.htm.url").read_text() == meta["url"]

    original, revision = records[0], records[1]
    assert original["notice_date"] is None  # receipt day is not a notice day
    details = json.loads(original["source_details"])
    assert details["agency_received_date"] == "2019-12-02"
    assert details["date_evidence_rule"] == "wi_notice_received_role_v1"
    assert details["source_capture"]["sha256"] == meta["sha256"]
    assert original["effective_date"] == "2020-01-31"
    assert original["employees_affected"] == 59
    assert original["source_url"] == meta["url"]
    assert original["is_amendment"] == 0
    assert revision["employer_name"] == "Regal Beloit America Inc."
    assert revision["is_amendment"] == 1
    assert json.loads(revision["source_details"])["agency_received_date"] == "2020-01-11"
    assert len({r["dedupe_key"] for r in records}) == 6

    # A replay from cache is identical; a changed cached page is refused.
    assert state_archives.fetch_wi_dwd(tmp_path) == records
    page = tmp_path / "archives" / "wi" / "dwd-2019.htm"
    page.write_bytes(page.read_bytes() + b"<!-- edited -->")
    with pytest.raises(ValueError, match="sha256"):
        state_archives.fetch_wi_dwd(tmp_path)


def test_distinct_same_key_rows_stay_distinct_for_the_replay_hold(tmp_path, monkeypatch):
    """Two filed Sears notices share employer, city and receipt day.

    The legacy WI key cannot separate them; both rows are returned with
    different hashes so the offline replay holds the key as conflicting
    rather than one silently replacing the other.
    """
    monkeypatch.setattr(state_archives, "WI_DWD_YEARS", [2016])
    monkeypatch.setattr(state_archives, "_download", _fake_download(2016))
    records = state_archives.fetch_wi_dwd(tmp_path)
    sears = [r for r in records if r["employer_name"] == "Sears Holding Company"]
    assert sorted(r["employees_affected"] for r in sears) == [3, 33]
    assert len({r["dedupe_key"] for r in sears}) == 1
    assert len({r["raw_record_hash"] for r in sears}) == 2


def test_rejects_a_page_without_the_notice_table(tmp_path, monkeypatch):
    monkeypatch.setattr(state_archives, "WI_DWD_YEARS", [2017])

    def download(url: str, dest: Path) -> bytes:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"<html>2017 WARN Notices and Updates</html>")
        return dest.read_bytes()

    monkeypatch.setattr(state_archives, "_download", download)
    assert state_archives.fetch_wi_dwd(tmp_path) == []
    assert not (tmp_path / "archives" / "wi" / "dwd-2017.htm").exists()
