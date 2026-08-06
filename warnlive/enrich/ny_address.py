"""Backfill New York site addresses from DOL's Tableau WARN dataset.

Our NY rows (upstream-processed) carry only a city, but the DOL WARN
dashboard's public Tableau workbook exposes the full register — with an
Impacted Site Address on every row — back to at least 2010. The CSV
endpoint returns only the default view (current year), so this fetches one
year at a time via the dashboard's year filter.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_CSV_URL = (
    "https://public.tableau.com/views/"
    "WorkerAdjustmentRetrainingNotificationWARN/WARN.csv"
    "?:showVizHome=n&YEAR(Date%20of%20WARN%20Notice)={year}"
)
FIRST_YEAR = 1990  # probe from here; empty years are skipped
_STREET_RE = re.compile(r"^\d{1,6}[\w./-]*\s+[A-Za-z]")
_WS = re.compile(r"\s+")


@dataclass
class NyRecord:
    company: str
    notice_date: str | None  # ISO
    start_date: str | None  # layoff/closure start
    address: str
    county: str


def _iso(value: str) -> str | None:
    value = (value or "").strip()
    try:
        return dt.date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _get(row: dict, name: str) -> str:
    # Tableau headers carry trailing spaces that shift between exports
    for k, v in row.items():
        if k and k.strip() == name:
            return (v or "").strip()
    return ""


def fetch_year(session: requests.Session, year: int, cache_dir: Path) -> str | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"ny_warn_{year}.csv"
    # the current and previous year keep changing; earlier ones are settled
    if path.exists() and year < dt.date.today().year - 1:
        return path.read_text()
    resp = session.get(_CSV_URL.format(year=year), timeout=120)
    if resp.status_code != 200 or not resp.text.strip():
        return None
    path.write_text(resp.text)
    return resp.text


def parse_year(text: str, year: int) -> list[NyRecord]:
    records = []
    for row in csv.DictReader(io.StringIO(text)):
        company = _get(row, "Business Legal Name")
        notice = _iso(_get(row, "Date of WARN Notice"))
        address = _WS.sub(" ", _get(row, "Impacted Site Address")).strip(" ,;")
        if not company or not notice or not notice.startswith(str(year)):
            continue
        if not _STREET_RE.search(address):
            continue
        records.append(NyRecord(
            company=company,
            notice_date=notice,
            start_date=_iso(_get(row, "Date Layoff/Closure Starts")),
            address=address,
            county=_get(row, "Impacted Site County"),
        ))
    return records


def collect_records(cache_dir: Path) -> list[NyRecord]:
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (warn-live)"
    records = []
    for year in range(FIRST_YEAR, dt.date.today().year + 1):
        try:
            text = fetch_year(session, year, cache_dir)
        except requests.RequestException as exc:
            logger.warning("ny-address: %s failed: %s", year, exc)
            continue
        if not text:
            continue
        parsed = parse_year(text, year)
        if parsed:
            logger.info("ny-address: %s -> %d records", year, len(parsed))
        records.extend(parsed)
    return records
