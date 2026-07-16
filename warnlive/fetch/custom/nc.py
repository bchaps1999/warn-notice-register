"""North Carolina — custom adapter (not covered by warn-scraper).

NC Commerce publishes a Salesforce-backed CSV, re-uploaded with a dated
filename on each refresh, linked from a year-specific report page. We scrape
the current year's report page and follow the files.nc.gov CSV link, then
add the archive years (2014-2025), which exist only as ruled-table PDFs
("WARN Summary by County/Parish") behind /open links on the archives page.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from io import StringIO
from pathlib import Path
from urllib.parse import urljoin

import pdfplumber
import requests
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

REPORT_PAGE = (
    "https://www.commerce.nc.gov/data-tools-reports/labor-market-data-tools/"
    "workforce-warn-reports/report-workforce-warn-summary-list-{year}"
)
ARCHIVES_PAGE = "https://www.commerce.nc.gov/documents/warn-summary-report-archives"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

CSV_COLUMNS = [
    "County",
    "Warn Number",
    "Date of Notice",
    "Date Received by NC",
    "Effective Date",
    "WARN Notice: WARN Notice Name",
    "WARN notice type",
    "Type of layoff or closure",
    "Number affected at this location",
    "Address 1",
    "City",
]


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    year = date.today().year

    # The current-year page may not exist yet in early January; fall back.
    for y in (year, year - 1):
        r = requests.get(REPORT_PAGE.format(year=y), headers=HEADERS, timeout=120)
        if r.ok:
            break
    r.raise_for_status()
    cache.write("nc/report_page.html", r.text)

    soup = BeautifulSoup(r.text, "html5lib")
    link = next(
        (
            a["href"]
            for a in soup.find_all("a", href=True)
            if "files.nc.gov" in a["href"] and ".csv" in a["href"].lower()
        ),
        None,
    )
    if link is None:
        raise ValueError("NC: no files.nc.gov CSV link found on report page")
    logger.debug("NC CSV link: %s", link)

    r = requests.get(link, headers=HEADERS, timeout=120)
    r.raise_for_status()
    cache.write("nc/latest.csv", r.text)
    rows = list(csv.DictReader(StringIO(r.text)))

    rows.extend(_scrape_archives(cache))

    data_path = data_dir / "nc.csv"
    with open(data_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return data_path


def _scrape_archives(cache: Cache) -> list[dict]:
    """Fetch and parse the 2014-2025 archive PDFs (cached: past years are
    immutable, so each PDF is downloaded once)."""
    r = requests.get(ARCHIVES_PAGE, headers=HEADERS, timeout=120)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html5lib")
    links = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "warn" in href.lower() and href.rstrip("/").endswith("/open"):
            links[href] = urljoin("https://www.commerce.nc.gov", href)

    rows: list[dict] = []
    for href, url in sorted(links.items()):
        name = "nc/archive-" + href.rstrip("/").split("/")[-2] + ".pdf"
        if cache.exists(name):
            pdf_path = Path(cache.path, name)
        else:
            rp = requests.get(url, headers=HEADERS, timeout=120)
            if not rp.ok or not rp.content.startswith(b"%PDF"):
                logger.warning("NC: skipping archive %s (%s)", url, rp.status_code)
                continue
            pdf_path = cache.write_binary(name, rp.content)
        parsed = _parse_archive_pdf(pdf_path)
        logger.debug("NC archive %s -> %d rows", name, len(parsed))
        rows.extend(parsed)
    return rows


def _parse_archive_pdf(path: Path) -> list[dict]:
    """The archive PDFs are fully ruled tables with the same columns as the
    CSV (wrapped header labels, an empty gutter column on each side)."""
    rows: list[dict] = []
    header: list[str] | None = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            for table in page.find_tables():
                for raw in table.extract():
                    cells = [" ".join((c or "").split()) for c in raw]
                    cells = [c for i, c in enumerate(cells) if not (i in (0, len(cells) - 1) and not c)]
                    if "Warn Number" in cells or "WARN No." in cells:
                        # The 2016-2021 vintage uses different labels; map
                        # them onto the modern CSV names.
                        header = [
                            {
                                "County/Parish": "County",
                                "WARN No.": "Warn Number",
                                "Notice Date": "Date of Notice",
                                "Received Date": "Date Received by NC",
                                "Company": "WARN Notice: WARN Notice Name",
                                "Layoff/Closure": "WARN notice type",
                                "Layoff/ Closure": "WARN notice type",
                                "No. Of Employees": "Number affected at this location",
                                "Address": "Address 1",
                            }.get(c, c)
                            for c in cells
                        ]
                        continue
                    if header is None or not any(cells):
                        continue
                    row = dict(zip(header, cells))
                    # Old vintage packs "Layoff Permanent" / "Closure Permanent"
                    # into one cell; split into type + permanence.
                    combined = row.get("WARN notice type", "")
                    if "Type of layoff or closure" not in row and combined:
                        parts = combined.replace("/", " ").split()
                        row["WARN notice type"] = parts[0] if parts else ""
                        row["Type of layoff or closure"] = " ".join(parts[1:])
                    if row.get("Warn Number", "").strip().isdigit():
                        rows.append(
                            {
                                "County": row.get("County", ""),
                                "Warn Number": row.get("Warn Number", ""),
                                "Date of Notice": row.get("Date of Notice", ""),
                                "Date Received by NC": row.get("Date Received by NC", ""),
                                "Effective Date": row.get("Effective Date", ""),
                                "WARN Notice: WARN Notice Name": row.get(
                                    "WARN Notice: WARN Notice Name", ""
                                ),
                                "WARN notice type": row.get("WARN notice type", ""),
                                "Type of layoff or closure": row.get(
                                    "Type of layoff or closure", ""
                                ),
                                "Number affected at this location": row.get(
                                    "Number affected at this location", ""
                                ),
                                "Address 1": row.get("Address 1", ""),
                                "City": row.get("City", ""),
                            }
                        )
    return rows
