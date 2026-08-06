"""Backfill Illinois effective dates from DCEO monthly WARN activity reports.

The IEBS export we scrape daily carries no layoff date for regular WARN
notices — its Impact Date column is a Trade Act petition field, filled on
~6% of rows. DCEO separately publishes a monthly WARN activity listing
(PDF through 2019, xlsx from 2020) whose per-notice FIRST LAYOFF DATE is
the effective date the export lacks. This module downloads those reports,
parses them, matches records to stored IL notices by zip + notified date
(falling back to folded employer name + date), and fills effective_date
where we have none.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://www.illinoisworknet.com/LayoffRecovery/Pages/ArchivedWARNReports.aspx"
_LINK_RE = re.compile(
    r"https://www\.illinoisworknet\.com/DownloadPrint/[^\"'&<>]+\.(?:pdf|PDF|xlsx)"
)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], 1)}
# Short forms seen in filenames (e.g. "Aug 2025 Monthly WARN Report.xlsx")
_MONTHS.update({m[:3].lower(): i for m in list(_MONTHS) for i in [_MONTHS[m]]})

_ZIP_RE = re.compile(r"(\d{5})(?:-\d{4})?\s*$")
_DATE_RE = re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})")


@dataclass
class ReportRecord:
    company: str
    address: str
    city_state_zip: str
    notified: str | None  # ISO date
    first_layoff: str | None  # ISO date
    ending_layoff: str | None  # ISO date
    source_file: str

    @property
    def zip5(self) -> str | None:
        m = _ZIP_RE.search(self.city_state_zip or "")
        return m.group(1) if m else None


def report_urls(session: requests.Session, years: set[int]) -> list[tuple[int, int, str]]:
    """Scrape the archive page; return (year, month, url) for requested years."""
    html = session.get(ARCHIVE_URL, timeout=60).text
    out = []
    for url in sorted(set(_LINK_RE.findall(html))):
        name = urllib.parse.unquote(url.rsplit("/", 1)[-1])
        m = re.match(r"([A-Za-z]+)\s*(\d{4})", name)
        if not m:
            continue
        month = _MONTHS.get(m.group(1).lower())
        year = int(m.group(2))
        if month and year in years:
            out.append((year, month, url))
    return sorted(out)


def fetch_report(session: requests.Session, url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / urllib.parse.unquote(url.rsplit("/", 1)[-1]).replace(" ", "_")
    if not path.exists() or path.stat().st_size == 0:
        resp = session.get(url, timeout=120)
        resp.raise_for_status()
        path.write_bytes(resp.content)
    return path


def _parse_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    m = _DATE_RE.search(str(value))
    if not m:
        return None
    month, day, year = (int(g) for g in m.groups())
    if year < 100:
        year += 2000 if year < 70 else 1900
    if not 1980 <= year <= 2035:  # transcription typos like "0200"
        return None
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return None


_TEXT_FIELD_RES = {
    "company": re.compile(r"COMPANY NAME:\s*(.*?)\s*(?:TYPE OF EVENT:|$)"),
    "address": re.compile(r"COMPANY ADDRESS:\s*(.*?)\s*(?:WARN NOTIFIED DATE:|$)"),
    "csz": re.compile(r"CITY, STATE, ZIP:\s*(.*?)\s*(?:#\s*WORKERS|$)"),
    "notified": re.compile(r"WARN NOTIFIED DATE:\s*([\d/-]*)"),
    "first": re.compile(r"FIRST LAYOFF DATE:\s*([\d/-]*)"),
    "ending": re.compile(r"ENDING LAYOFF DATE:\s*([\d/-]*)"),
}


def _parse_page_text(text: str, source: str) -> list[ReportRecord]:
    """Pre-2005 reports draw no table rules, so extract_tables sees nothing;
    the text layout interleaves left and right columns on shared lines
    ('COMPANY NAME: X TYPE OF EVENT: Y'), which these regexes split apart.
    Trailing blank template records parse as empty and are dropped."""
    records = []
    # Each record starts at COMPANY NAME:; keep the delimiter with its block
    blocks = re.split(r"(?=COMPANY NAME:)", text)
    for block in blocks:
        if not block.startswith("COMPANY NAME:"):
            continue
        fields = {}
        for line in block.splitlines():
            for key, pattern in _TEXT_FIELD_RES.items():
                m = pattern.search(line)
                if m and m.group(1).strip():
                    fields.setdefault(key, m.group(1).strip())
        if not fields.get("company"):
            continue
        records.append(ReportRecord(
            company=fields["company"],
            address=fields.get("address", ""),
            city_state_zip=fields.get("csz", ""),
            notified=_parse_date(fields.get("notified")),
            first_layoff=_parse_date(fields.get("first")),
            ending_layoff=_parse_date(fields.get("ending")),
            source_file=source,
        ))
    return records


def parse_pdf(path: Path) -> list[ReportRecord]:
    import pdfplumber

    records = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            tables = page.extract_tables()
            if not tables:
                records.extend(_parse_page_text(page.extract_text() or "", path.name))
                continue
            for table in tables:
                fields: dict[str, str] = {}
                for row in table:
                    cells = [c.strip() if c else "" for c in row]
                    # left key/value pair and right key/value pair per row
                    for i, cell in enumerate(cells):
                        if cell.endswith(":"):
                            value = next((c for c in cells[i + 1:] if c and not c.endswith(":")), "")
                            fields.setdefault(cell.rstrip(":").upper(), value)
                company = fields.get("COMPANY NAME", "")
                if not company:
                    continue
                records.append(ReportRecord(
                    company=company,
                    address=fields.get("COMPANY ADDRESS", ""),
                    city_state_zip=fields.get("CITY, STATE, ZIP", ""),
                    notified=_parse_date(fields.get("WARN NOTIFIED DATE")
                                         or fields.get("WARN RECEIVED DATE")),
                    first_layoff=_parse_date(fields.get("FIRST LAYOFF DATE")),
                    ending_layoff=_parse_date(fields.get("ENDING LAYOFF DATE")),
                    source_file=path.name,
                ))
    return records


def parse_xlsx(path: Path) -> list[ReportRecord]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        return []
    idx = {str(h).rstrip(": ").strip().upper(): i
           for i, h in enumerate(header) if h}

    def col(row, *names):
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return None

    records = []
    for row in rows:
        company = col(row, "COMPANY NAME")
        if not company or not str(company).strip():
            continue
        records.append(ReportRecord(
            company=str(company).strip(),
            address=str(col(row, "COMPANY ADDRESS") or "").strip(),
            city_state_zip=str(col(row, "CITY, STATE, ZIP") or "").strip(),
            notified=_parse_date(col(row, "WARN RECEIVED DATE", "WARN NOTIFIED DATE")),
            first_layoff=_parse_date(col(row, "FIRST LAYOFF DATE")),
            ending_layoff=_parse_date(col(row, "ENDING LAYOFF DATE")),
            source_file=path.name,
        ))
    return records


def parse_report(path: Path) -> list[ReportRecord]:
    if path.suffix.lower() == ".xlsx":
        return parse_xlsx(path)
    return parse_pdf(path)


def collect_records(years: set[int], cache_dir: Path) -> list[ReportRecord]:
    session = requests.Session()
    session.headers["User-Agent"] = "warn-live/1.0"
    records = []
    for year, month, url in report_urls(session, years):
        try:
            path = fetch_report(session, url, cache_dir)
            parsed = parse_report(path)
        except Exception as exc:  # noqa: BLE001 — one bad report shouldn't sink the run
            logger.warning("il-effective: %s-%02d failed: %s", year, month, exc)
            continue
        logger.info("il-effective: %s -> %d records", path.name, len(parsed))
        records.extend(parsed)
    return records
