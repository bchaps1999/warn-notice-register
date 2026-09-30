"""District of Columbia — patched from upstream warn-scraper dc.py.

DOES publishes one page per year. The current year's page links the
earlier years' pages. Upstream took those links from the page's first
``div.field-items``. By 2026-09-30 that div held only the sidebar's Rapid
Response links, one of them relative (``/page/rapid-response``), so upstream
requested a relative URL and failed (``MissingSchema``); the year links
now sit in the page body without that wrapper, and two of them point at
``/node/<id>`` rather than a ``-YYYY`` slug.

Patch: take the year links from anywhere on the page by their text
("Industry Closings and Layoffs WARN Notifications YYYY") and resolve each
href against the DOES page it came from. Everything else is upstream's:
the same pages, the same first table per page, the same cell cleaning, the
header kept from the current page only, and the same raw columns.

Network: fetches the current (or previous) year's page and every linked
year page; pages are cached under ``{cache_dir}/dc/``. Writes
``{data_dir}/dc.csv``.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

PAGE_URL = "https://does.dc.gov/page/industry-closings-and-layoffs-warn-notifications-{year}"
_YEAR_LINK = re.compile(r"industry\s+closings\s+and\s+layoffs\s+warn\s+notifications\s+((?:19|20)\d{2})",
                        re.IGNORECASE)
# A June 2025 entry wraps a cell's text in a nested table (upstream's patch).
_WEIRD_TABLE = r"(\s+<table>\s+<tbody>\s+<tr>\s+<td>)(.*)(</td>\s+</tr>\s+</tbody>\s+</table>\s+)"
_ARCHIVED_2014 = (
    "https://web.archive.org/web/20170210010137/http://does.dc.gov/page/"
    "industry-closings-and-layoffs-warn-notifications-closure%202014"
)


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    year = datetime.today().year
    targetfile = Path(cache_dir) / f"dc/{year}.html"
    url = PAGE_URL.format(year=year)
    success, _ = utils.save_if_good_url(targetfile, url)
    if not success:  # no page yet for a new year
        targetfile = Path(cache_dir) / f"dc/{year - 1}.html"
        url = PAGE_URL.format(year=year - 1)
        success, _ = utils.save_if_good_url(targetfile, url)
    if not success:
        raise ValueError(f"DC: no WARN page for {year} or {year - 1}")
    root_html = _unwrap(cache.read("/".join(str(targetfile).split("/")[-2:])), url)

    html_list = [root_html]
    for href in year_links(root_html, url).values():
        r = utils.get_url(href)
        r.encoding = "utf-8"
        html = r.text
        cache.write(f"dc/{uuid.uuid5(uuid.NAMESPACE_URL, href)}.html", html)
        html_list.append(_unwrap(html, href))

    data_path = Path(data_dir) / "dc.csv"
    utils.write_rows_to_csv(data_path, parse_pages(html_list))
    return data_path


def year_links(html: str, page_url: str) -> dict[str, str]:
    """Absolute URLs of the linked year pages, keyed by year, in page order."""
    soup = BeautifulSoup(html, "html5lib")
    links: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        match = _YEAR_LINK.search(" ".join(a.get_text(" ").split()))
        if match and match.group(1) not in links:
            links[match.group(1)] = urljoin(page_url, a["href"].strip())
    if not links:
        raise ValueError("DC: no year-page links found on the WARN page")
    # warn-scraper #238: the 2014 link once pointed at the 2018 page.
    if "2014" in links and links.get("2014") == links.get("2018"):
        logger.warning("DC: 2014 link is the same as 2018 link, using archived 2014")
        links["2014"] = _ARCHIVED_2014
    return links


def parse_pages(html_list: list[str]) -> list[list[str]]:
    """Rows of each page's first table; the header row from the first page only."""
    output_rows: list[list[str]] = []
    for i, html in enumerate(html_list):
        tables = BeautifulSoup(html, "html5lib").find_all("table")
        if not tables:
            raise ValueError(f"DC: page {i} has no table")
        rows = tables[0].find_all("tr")
        if i > 0:
            rows = rows[1:]
        for row in rows:
            cells = [_clean_text(c.text) for c in row.find_all(["td", "th"])]
            if any(cells):
                output_rows.append(cells)
    return output_rows


def _unwrap(html: str, url: str) -> str:
    after, changes = re.subn(_WEIRD_TABLE, r"\2", html)
    if changes:
        logger.debug("%d changes made to %s", changes, url)
    return after


def _clean_text(text: str | None) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"\n", " ", text)).strip()
