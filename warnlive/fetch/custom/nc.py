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
import re
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
    # An exception on the first try must reach the fallback too — a timeout
    # is no more proof the prior year is gone than a 404 is.
    r = None
    last_exc: Exception | None = None
    for y in (year, year - 1):
        try:
            r = requests.get(REPORT_PAGE.format(year=y), headers=HEADERS, timeout=120)
        except requests.RequestException as exc:
            last_exc = exc
            continue
        if r.ok:
            break
    if r is None:
        raise ValueError(f"NC: report page unreachable for {year} and {year - 1}") from last_exc
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
    # Decoded explicitly: requests guesses ISO-8859-1 for text/csv with no
    # charset, and a mojibake employer name mints a new dedupe key.
    text = r.content.decode("utf-8-sig", errors="replace")
    cache.write("nc/latest.csv", text)
    rows = list(csv.DictReader(StringIO(text)))

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
        if not parsed:
            # The 2014-2017 vintage has no ruled table — plain text columns.
            parsed = _parse_text_archive_pdf(pdf_path)
        logger.debug("NC archive %s -> %d rows", name, len(parsed))
        rows.extend(parsed)
    return rows


_TEXT_COLUMNS = ["Notice", "Effective", "Company", "City", "#", "Layoff"]


def _parse_text_archive_pdf(path: Path) -> list[dict]:
    """Parse the 2014-2017 "WARN Notice - Summary Count" PDFs: no table
    rulings, just left-aligned text columns. Column boundaries come from the
    header labels' x positions; each word lands in the rightmost column
    starting left of it."""
    rows: list[dict] = []
    edges: list[float] | None = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            lines: dict[int, list] = {}
            for w in words:
                lines.setdefault(round(w["top"]), []).append(w)
            for _, ws in sorted(lines.items()):
                ws.sort(key=lambda w: w["x0"])
                texts = [w["text"] for w in ws]
                if texts[:2] == ["Notice", "Date"] and "Company" in texts:
                    starts = {}
                    for w in ws:
                        if w["text"] in _TEXT_COLUMNS and w["text"] not in starts:
                            starts[w["text"]] = w["x0"]
                    if len(starts) == len(_TEXT_COLUMNS):
                        edges = [starts[c] for c in _TEXT_COLUMNS]
                    continue
                if edges is None or not re.match(r"\d{2}/\d{2}/\d{4}", texts[0]):
                    continue
                cols: list[list[str]] = [[] for _ in edges]
                for w in ws:
                    idx = 0
                    for i, e in enumerate(edges):
                        if w["x0"] >= e - 4:
                            idx = i
                    cols[idx].append(w["text"])
                notice_date, effective, company, city, jobs, kind = (
                    " ".join(c) for c in cols
                )
                if not company:
                    continue
                # The type text starts left of its header label, so it lands
                # in the jobs column; split digits from the rest.
                m = re.match(r"^([\d,]*)\s*(.*)$", jobs)
                jobs, kind = m.group(1), f"{m.group(2)} {kind}".strip()
                type_parts = kind.replace("/", " ").split()
                rows.append(
                    {
                        "County": "",
                        "Warn Number": "",
                        "Date of Notice": notice_date,
                        "Date Received by NC": "",
                        "Effective Date": effective,
                        "WARN Notice: WARN Notice Name": company,
                        "WARN notice type": type_parts[0] if type_parts else "",
                        "Type of layoff or closure": " ".join(type_parts[1:]),
                        "Number affected at this location": jobs.replace(",", ""),
                        "Address 1": "",
                        "City": city,
                    }
                )
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
