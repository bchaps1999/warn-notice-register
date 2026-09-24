"""Florida's year-addressed fetch must preserve the prior CSV on failure."""

import csv
from datetime import date

import pytest

from warnlive.fetch.patches import fl


class _Clock:
    @staticmethod
    def today():
        return date(2020, 1, 1)


def _row():
    return ["Company", "01-01-20", "03-01-20\nthru\n03-31-20", "10", "Retail", ""]


def test_direct_html_year_endpoint_keeps_agency_columns(monkeypatch):
    seen = []
    html = "<table><thead><tr>" + "".join(
        f"<th>{value}</th>" for value in fl.upstream.FIELDS
    ) + "</tr></thead><tbody><tr>" + "".join(
        f"<td>{value}</td>" for value in _row()
    ) + "</tr></tbody></table>"
    class Response:
        text = html

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, timeout):
            seen.append((url, timeout))
            return Response()

    class Cache:
        def exists(self, _key):
            return False

        def write(self, _key, _html):
            pass

    rows = fl._html_rows(Cache(), 2026, Session())

    assert seen == [("https://reactwarn.floridajobs.org/WarnList/Records?year=2026&page=1", 45)]
    assert len(rows) == 1
    assert rows[0][:2] == ["Company", "01-01-20"]


def test_current_year_empty_table_is_allowed_but_prior_year_is_not(monkeypatch):
    monkeypatch.setattr(fl, "date", _Clock)
    page = ("<h1>2020 Worker Adjustment and Retraining Notification Notices</h1>"
            "<p>0 Record(s) found</p><table><tbody></tbody></table>")
    monkeypatch.setattr(fl, "_html_pages", lambda *_: [page])
    assert fl._html_rows(object(), 2020, object()) == []
    with pytest.raises(ValueError, match="yielded no rows"):
        fl._html_rows(object(), 2019, object())


def test_http_failure_is_bounded_and_does_not_write_cache():
    class Cache:
        writes = 0

        def exists(self, _key):
            return False

        def write(self, *_args):
            self.writes += 1

    class Response:
        def raise_for_status(self):
            raise RuntimeError("403 Forbidden")

    class Session:
        calls = 0

        def get(self, _url, timeout):
            self.calls += 1
            assert timeout == 45
            return Response()

    cache, session = Cache(), Session()
    with pytest.raises(RuntimeError, match="403 Forbidden"):
        fl._html_pages(cache, 2026, session)
    assert session.calls == 1
    assert cache.writes == 0


def test_year_failure_leaves_existing_florida_csv_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "date", _Clock)
    monkeypatch.setattr(fl.niquests, "Session", lambda: object())
    monkeypatch.setattr(fl, "_pdf_rows", lambda *_: [_row()] * 125)

    def html(_cache, year, _session):
        if year == 2020:
            raise ValueError("year page failed")
        return [_row()] * 125

    monkeypatch.setattr(fl, "_html_rows", html)
    output = tmp_path / "raw"
    output.mkdir()
    previous = output / "fl.csv"
    previous.write_bytes(b"previous accepted capture\n")

    with pytest.raises(ValueError, match="year page failed"):
        fl.scrape(output, tmp_path / "cache")

    assert previous.read_bytes() == b"previous accepted capture\n"
    assert not (output / "fl.csv.tmp").exists()


def test_complete_years_replace_florida_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "date", _Clock)
    monkeypatch.setattr(fl.niquests, "Session", lambda: object())
    seen = []
    monkeypatch.setattr(
        fl, "_pdf_rows", lambda _cache, year, _session:
        seen.append(("pdf", year)) or [_row()] * 125,
    )
    monkeypatch.setattr(
        fl, "_html_rows", lambda _cache, year, _session:
        seen.append(("html", year)) or [_row()] * 125,
    )

    output = fl.scrape(tmp_path / "raw", tmp_path / "cache")

    assert seen == [("pdf", year) for year in range(2015, 2019)] + [
        ("html", 2019), ("html", 2020),
    ]
    with output.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 750
    assert set(rows[0]) == set(fl.upstream.CSV_HEADERS)
