"""DC's WARN page moved its year links and gained a relative sidebar link;
the patch reads year links by their text and resolves them."""

import csv
from pathlib import Path

import pytest

from warnlive.fetch import patches
from warnlive.fetch.patches import dc

ROOT_URL = "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2026"
HEADER = "<tr><td>Notice Date</td><td>Organization Name</td><td>Number to<br>Employees Affected</td>" \
         "<td>Effective Layoff Date</td><td>Code Type</td></tr>"


def _page(rows: str, extra: str = "") -> str:
    return f"""<html><body>
    <div class="field-items"><a href="/page/rapid-response">Rapid Response</a></div>
    <div class="field-item"><table><tbody>{HEADER}{rows}</tbody></table>
    {extra}</div></body></html>"""


ROOT = _page(
    "<tr><td>February 2, 2026</td><td>Elior North America</td><td>76</td>"
    "<td>June 30, 2026</td><td>1</td></tr>",
    '<p><a href="https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2025">'
    "Industry Closings and Layoffs WARN Notifications 2025</a></p>"
    '<p><a href="/node/1468786">Industry Closings and Layoffs Warn Notifications 2020</a></p>',
)


def test_year_links_are_found_by_text_and_resolved_against_the_page():
    assert dc.year_links(ROOT, ROOT_URL) == {
        "2025": "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2025",
        "2020": "https://does.dc.gov/node/1468786",
    }
    with pytest.raises(ValueError, match="no year-page links"):
        dc.year_links(_page(""), ROOT_URL)


def test_scrape_writes_upstream_rows_from_every_year_page(tmp_path, monkeypatch):
    pages = {
        "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-2025": _page(
            "<tr><td>March 3, 2025</td><td>Acme</td><td>60</td><td>May 1, 2025</td><td>1</td></tr>"),
        "https://does.dc.gov/node/1468786": _page(
            "<tr><td>April 1, 2020</td><td>Hotel\n  Co</td><td>120</td><td>April 1, 2020</td>"
            "<td>2</td></tr><tr><td></td><td></td></tr>"),
    }
    requested = []

    def save_if_good_url(path, url):
        requested.append(url)
        if not url.endswith(f"-{dc.datetime.today().year}"):
            return False, None
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(ROOT)
        return True, ROOT

    class Response:
        def __init__(self, text):
            self.text, self.encoding = text, None

    def get_url(url, **_kwargs):
        assert url.startswith("https://"), url
        requested.append(url)
        return Response(pages[url])

    monkeypatch.setattr(dc.utils, "save_if_good_url", save_if_good_url)
    monkeypatch.setattr(dc.utils, "get_url", get_url)
    path = dc.scrape(tmp_path / "data", tmp_path / "cache")
    rows = list(csv.reader(path.open()))
    assert rows == [
        ["Notice Date", "Organization Name", "Number toEmployees Affected",
         "Effective Layoff Date", "Code Type"],
        ["February 2, 2026", "Elior North America", "76", "June 30, 2026", "1"],
        ["March 3, 2025", "Acme", "60", "May 1, 2025", "1"],
        ["April 1, 2020", "Hotel Co", "120", "April 1, 2020", "2"],
    ]
    assert "https://does.dc.gov/page/rapid-response" not in requested
    assert len(list((tmp_path / "cache/dc").glob("*.html"))) == 3


def test_dc_dispatches_to_the_patch():
    import importlib

    assert importlib.import_module(f"{patches.__name__}.dc") is dc
