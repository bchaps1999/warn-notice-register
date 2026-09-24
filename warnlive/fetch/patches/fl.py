"""Florida WARN capture from the agency's year-specific records endpoints.

The upstream scraper discovers years from a landing-page selector that no
longer returns links. Florida's REACT site still exposes 2015–2018 PDFs and
2019-onward records pages under stable, year-addressed URLs. Keep the upstream
table/PDF parsers, but require every year to yield rows before replacing the
working CSV.
"""

from __future__ import annotations

import os
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import niquests
import pdfplumber
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache
from warn.scrapers import fl as upstream

BASE = "https://reactwarn.floridajobs.org/WarnList/"
FIRST_PDF_YEAR = 2015
LAST_PDF_YEAR = 2018
FIRST_HTML_YEAR = 2019


def _pdf_rows(cache_dir: Path, year: int, session) -> list[list[str]]:
    path = cache_dir / "fl" / f"{year}.pdf"
    if not path.is_file():
        url = f"{BASE}viewPreviousYearsPDF?year={year}"
        response = session.get(url, timeout=120)
        response.raise_for_status()
        if not response.content.startswith(b"%PDF"):
            raise ValueError(f"Florida {year} archive did not return a PDF")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
    rows: list[list[str]] = []
    with pdfplumber.open(path) as pdf:
        for page_index, page in enumerate(pdf.pages):
            table = page.extract_table(table_settings={}) or []
            if page_index == 0 and table:
                table.pop(0)
            rows.extend(upstream._clean_table(table, rows))
    if not rows:
        raise ValueError(f"Florida {year} archive yielded no rows")
    return rows


def _html_pages(cache: Cache, year: int, session) -> list[str]:
    """Follow only same-year pages, with bounded requests and pagination."""
    pages = []
    for page in range(1, 101):
        url = f"{BASE}Records?year={year}&page={page}"
        key = f"fl/{year}_page_{page}.html"
        fetched = False
        if year < date.today().year - 1 and cache.exists(key):
            html = cache.read(key)
        else:
            response = session.get(url, timeout=45)
            response.raise_for_status()
            html = response.text
            fetched = True
        html = html.replace("</br>", "\n")
        soup = BeautifulSoup(html, "html.parser")
        if soup.select_one("table tbody") is None:
            raise ValueError(f"Florida {year} page {page} has no notice table")
        headers = [cell.get_text(" ", strip=True) for cell in soup.select("table thead th")]
        if headers[:len(upstream.FIELDS)] != upstream.FIELDS:
            raise ValueError(f"Florida {year} page {page} table columns changed: {headers!r}")
        if fetched:
            cache.write(key, html)
        pages.append(html)
        footer = soup.find("tfoot")
        next_page = None
        if footer is not None:
            for anchor in footer.select("a[href]"):
                candidate = urljoin(BASE, anchor["href"])
                parsed = urlparse(candidate)
                query = parse_qs(parsed.query)
                if (parsed.scheme == "https" and
                        parsed.netloc == "reactwarn.floridajobs.org" and
                        parsed.path.lower() == "/warnlist/records" and
                        query.get("year") == [str(year)] and
                        query.get("page") == [str(page + 1)]):
                    next_page = candidate
                    break
        if next_page is None:
            return pages
    raise ValueError(f"Florida {year} pagination exceeded 100 pages")


def _html_rows(cache: Cache, year: int, session) -> list[list[str]]:
    pages = _html_pages(cache, year, session)
    rows = upstream._html_to_rows(pages)
    if not rows:
        text = BeautifulSoup(pages[0], "html.parser").get_text(" ", strip=True)
        if year == date.today().year and len(pages) == 1 and re.search(
            r"\b0\s+Record\(s\)\s+found\b", text, re.I,
        ) and re.search(
            rf"\b{year}\s+Worker Adjustment and Retraining Notification Notices\b",
            text, re.I,
        ):
            return []
        raise ValueError(f"Florida {year} records page yielded no rows")
    return rows


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    data_dir = Path(data_dir)
    cache_dir = Path(cache_dir)
    cache = Cache(cache_dir)
    session = niquests.Session()
    rows = []
    for year in range(FIRST_PDF_YEAR, LAST_PDF_YEAR + 1):
        rows.extend(_pdf_rows(cache_dir, year, session))
    for year in range(FIRST_HTML_YEAR, date.today().year + 1):
        rows.extend(_html_rows(cache, year, session))
    if len(rows) < 500:
        raise ValueError(f"Florida year pages yielded only {len(rows)} rows")
    if any(len(row) < len(upstream.CSV_HEADERS) for row in rows):
        raise ValueError("Florida year page has a short data row")

    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "fl.csv"
    temporary = data_dir / "fl.csv.tmp"
    try:
        utils.write_dict_rows_to_csv(
            temporary, upstream.CSV_HEADERS,
            [dict(zip(upstream.FIELDS, row)) for row in rows],
            extrasaction="ignore",
        )
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target
