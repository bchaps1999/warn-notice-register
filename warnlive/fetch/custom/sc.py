"""South Carolina WARN reports.

The current SC PDF has a proper seven-column table: county, *notice* date,
layoff date/range, worker count, action, and worksite address.  The upstream
scraper treated every date-shaped cell alike, retaining the last one as an
ambiguous ``date``.  Keep the newer layout's fields separate.  Older reports
only expose a projected date, so they are emitted as ``legacy_date`` rather
than pretending it is a notice date.
"""

from __future__ import annotations

import csv
import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

PAGE_URL = "https://scworks.org/employer/employer-programs/risk-closing/layoff-notification-reports"
COLUMNS = [
    "company", "county", "notice_date", "effective_date", "effective_end_date",
    "impacted", "action_type", "address", "legacy_date", "naics", "source",
    "source_page", "source_row",
]
# Do not require a word boundary: tightly ruled PDFs can place the first
# impacted-count glyph immediately after a notice date (``8/21/20251``).
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")
_LEGACY_DATE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/+\d{2,4}|\d{1,2}/\d{4}|"
    r"(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{4})\b",
    re.I,
)
_NAICS = re.compile(r"^\d{5,6}$")
_COUNT = re.compile(r"^\d{1,6}$")


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    """Download all linked reports and write a lossless-enough raw CSV."""
    cache = Cache(cache_dir)
    response = utils.get_url(PAGE_URL, verify=False)
    cache.write("sc/source.html", response.text)
    soup = BeautifulSoup(response.text, "html.parser")
    reports: dict[int, str] = {}
    for link in soup.find_all("a", href=True):
        label = link.get_text(" ", strip=True)
        match = re.match(r"(\d{4})", label)
        if match and ".pdf" in link["href"].lower():
            reports.setdefault(int(match.group(1)), link["href"])
    if not reports:
        raise ValueError("SC: no year-labelled PDF reports found")

    rows: list[dict[str, str]] = []
    current_year = datetime.now().year
    for year, href in sorted(reports.items()):
        key = f"sc/{year}.pdf"
        path = Path(cache.path) / key if cache.exists(key) and year < current_year - 1 else cache.download(
            key, urljoin("https://scworks.org/", href), verify=False
        )
        for ordinal, row in enumerate(parse_pdf(Path(path)), 1):
            row["source"] = key
            row["source_row"] = str(ordinal)
            rows.append(row)

    out = Path(data_dir) / "sc.csv"
    with open(out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def parse_pdf(path: Path) -> list[dict[str, str]]:
    """Extract report rows, supporting current and pre-notice-date layouts."""
    rows: list[dict[str, str]] = []
    with pdfplumber.open(path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            table = page.extract_table()
            if not table:
                continue
            header = [" ".join((cell or "").split()).lower() for cell in table[0]]
            parsed = (
                _current_rows(table[1:], _notice_dates(page))
                if "notice date" in header and "layoff/closure date" in header
                else _legacy_rows(table)
            )
            for row in parsed:
                row["source_page"] = str(page_number)
            rows.extend(parsed)
    return rows


def cached_csv(cache_dir: Path, out_path: Path) -> int:
    """Materialize cached annual PDFs without a live fetch (for candidate builds).

    The annual artifact and row ordinal are retained as SC source identity.
    This writes only the caller-supplied output path, never the normal raw CSV.
    """
    paths = sorted(Path(cache_dir).glob("*.pdf"))
    if not paths:
        raise ValueError(f"no cached SC PDFs in {cache_dir}")
    rows: list[dict[str, str]] = []
    for path in paths:
        for ordinal, row in enumerate(parse_pdf(path), 1):
            rows.append({**row, "source": f"sc/{path.name}", "source_row": str(ordinal)})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _notice_dates(page) -> list[str]:
    """Read the notice-date column even where it is overprinted by county.

    SC's ``Statewide - Multiple Counties`` label protrudes into the next
    column in the 2026 PDF.  Table extraction interleaves its letters with a
    real date (``1C/8o/u2n0t2ie6s``).  The date glyphs remain positioned in
    the notice-date column, so filtering that column to digit/slash glyphs
    recovers the source date without guessing it from the layoff date.
    """
    words = page.extract_words()
    notice = next((word for word in words if word["text"] == "Notice"), None)
    layoff = next(
        (word for word in words if word["text"] == "Layoff/Closure" and notice and word["x0"] > notice["x0"]),
        None,
    )
    if notice is None or layoff is None:
        return []
    lines: dict[float, list[dict]] = {}
    for char in page.chars:
        if not notice["x0"] <= char["x0"] < layoff["x0"]:
            continue
        top = next((key for key in lines if abs(key - char["top"]) <= 2), char["top"])
        lines.setdefault(top, []).append(char)
    dates: list[str] = []
    for chars in lines.values():
        text = "".join(
            char["text"] for char in sorted(chars, key=lambda char: char["x0"])
            if char["text"].isdigit() or char["text"] == "/"
        )
        found = _DATE.search(text)
        if found:
            dates.append(found.group())
    return dates


def _current_rows(
    table: list[list[str | None]], notice_dates: list[str] | None = None,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for index, raw in enumerate(table):
        cells = [" ".join((cell or "").split()) for cell in raw]
        if len(cells) < 7 or not cells[0] or cells[0].lower().startswith("total warn"):
            continue
        notice = _DATE.search(cells[2])
        if notice is None and notice_dates and index < len(notice_dates):
            notice = _DATE.search(notice_dates[index])
        effective = _DATE.findall(cells[3])
        if not notice or not _COUNT.fullmatch(cells[4]):
            continue
        county = cells[1]
        # pdfplumber interleaves the tail of this long county label with the
        # notice-date column.  It is a rendering defect, not a new field.
        if county.startswith("Statewide - Mu") and "ltiple" in cells[2]:
            county = "Statewide - Multiple Counties"
        rows.append({
            "company": cells[0], "county": county,
            "notice_date": notice.group(), "effective_date": effective[0] if effective else "",
            "effective_end_date": effective[1] if len(effective) > 1 else "",
            "impacted": cells[4], "action_type": cells[5], "address": cells[6],
        })
    return rows


def _legacy_rows(table: list[list[str | None]]) -> list[dict[str, str]]:
    """Best-effort old-layout reader; its one published date is not notice date."""
    rows: list[dict[str, str]] = []
    for raw in table:
        cells = [" ".join((cell or "").split()) for cell in raw]
        values = [cell for cell in cells if cell]
        dates = [date for value in values for date in _LEGACY_DATE.findall(value)]
        jobs = next((value for value in values if _COUNT.fullmatch(value)), "")
        if len(values) < 4 or not dates or not jobs:
            continue
        # Old tables consistently place company then location.  Do not infer
        # county or address from a layout that does not label either one.
        rows.append({
            "company": values[0], "county": values[1], "legacy_date": dates[-1],
            "impacted": jobs,
            "naics": next((v for v in values if _NAICS.fullmatch(v)), ""),
        })
    return rows
