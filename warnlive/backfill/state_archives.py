"""Historical backfill from official state archives via the Wayback Machine.

Some states published WARN histories that their current portals no longer
carry. Where the state's own artifacts survive — as agency spreadsheets or
agency web pages captured by the Internet Archive — we ingest them with the
exact Wayback artifact URL as source_url, so every notice still traces to a
state-published document.

Sources here (found 2026-07-26; see docs / plan notes):
  WI: DWD WORKnet annual "Plant Closing / Mass Layoff (PCML) log" .xls
      files, 1996-2015. Clean one-file-per-year spreadsheets.
  FL: predecessor app floridajobs.org/react/warn.asp?year=YYYY, yearly HTML
      tables 1997-2015 (1997 has its own page name).
  CA: EDD yearly WARN report PDFs (eddwarncn{YY}.pdf and sorted variants),
      2000-2014. Ruled-table PDFs listing company/location/jobs/layoff
      date — no notice date, so rows land with notice_date NULL and the
      dedupe key falls back to the effective date.

Ingestion uses the same strict month-gap rule as the BLN gap-fill: a row
only enters months where the state currently has zero notices, so archive
overlap with live data cannot mint near-duplicates.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from time import sleep

import xlrd
from bs4 import BeautifulSoup

from warnlive.normalize.engine import _clean_text, _dedupe_key, _record_hash

logger = logging.getLogger("warnlive")

WI_ORIGINAL = (
    "http://worknet.wisconsin.gov/worknet_info/downloads/PCML/{year}pcml_log.xls"
)
WI_YEARS = range(1996, 2016)
CDX_API = "https://web.archive.org/cdx/search/cdx"
XLS_MAGIC = b"\xd0\xcf\x11\xe0"

FL_URL = (
    "https://web.archive.org/web/{year_after}0601000000/"
    "http://www.floridajobs.org/react/warn.asp?year={year}"
)
# 1997 has its own page name; the timestamp-guess URL resolves to a late
# empty capture, so its captures are resolved via CDX instead.
FL_1997_ORIGINAL = "http://www.floridajobs.org/react/1997warn.asp"
FL_YEARS = range(1997, 2016)


def _canonical(rec: dict, raw: dict) -> dict:
    rec["employer_name"] = _clean_text(rec.get("employer_name"))
    rec["location"] = _clean_text(rec.get("location"))
    rec.setdefault("is_temporary", None)
    rec.setdefault("is_amendment", 0)
    rec.setdefault("source_notice_id", None)
    rec["raw_extra"] = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    # Sources that publish no notice date (CA's yearly PDFs) key on the
    # effective date instead, else same-employer same-city rows with
    # different layoff dates would collide on an all-empty date slot.
    rec["dedupe_key"] = _dedupe_key(
        dict(rec, notice_date=rec.get("notice_date") or rec.get("effective_date"))
    )
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def _download(url: str, dest: Path) -> bytes | None:
    """Politely fetch a Wayback artifact, caching to dest; None on failure.
    Uses urllib: niquests intermittently gets served Wayback's HTML error
    shell for artifact URLs that fetch fine with a plain client."""
    import urllib.request

    if dest.exists():
        return dest.read_bytes()
    sleep(1)  # pace archive.org
    req = urllib.request.Request(
        url, headers={"User-Agent": "warn-live archive backfill"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            content = resp.read()
    except Exception as exc:  # noqa: BLE001
        logger.warning("archive fetch failed %s (%s)", url, exc)
        return None
    if not content:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return content


# --- Wisconsin ---------------------------------------------------------------

_WI_TYPE = {"clos": "closure", "layoff": "mass_layoff"}


def _wayback_captures(original_url: str) -> list[str]:
    """All 200-status capture URLs of original_url, newest first, as id_
    URLs (raw archived bytes, no Wayback HTML wrapper)."""
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode(
        {"url": original_url, "output": "json", "filter": "statuscode:200"}
    )
    req = urllib.request.Request(
        f"{CDX_API}?{query}", headers={"User-Agent": "warn-live archive backfill"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            rows = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        logger.warning("CDX lookup failed for %s (%s)", original_url, exc)
        return []
    return [
        f"https://web.archive.org/web/{r[1]}id_/{original_url}"
        for r in rows[1:]
    ][::-1]


def fetch_wi(cache_dir: Path) -> list[dict]:
    records: list[dict] = []
    for year in WI_YEARS:
        content = url = None
        dest = cache_dir / "archives" / "wi" / f"{year}.xls"
        for candidate in _wayback_captures(WI_ORIGINAL.format(year=year)):
            content = _download(candidate, dest)
            if content is not None and content.startswith(XLS_MAGIC):
                url = candidate
                break
            dest.unlink(missing_ok=True)  # wrapper/error page, not the xls
            content = None
        if content is None:
            logger.warning("WI %s: no usable archived xls found", year)
            continue
        try:
            wb = xlrd.open_workbook(file_contents=content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("WI %s: not a readable xls (%s)", year, exc)
            continue
        sheet = wb.sheets()[0]
        header_row, cols = None, {}
        for r in range(min(6, sheet.nrows)):
            values = [str(sheet.cell_value(r, c)).lower() for c in range(sheet.ncols)]
            if any("notice received" in v for v in values):
                header_row = r
                for c, v in enumerate(values):
                    if "notice received" in v:
                        cols["date"] = c
                    elif "company" in v:
                        cols["company"] = c
                    elif "location" in v:
                        cols["location"] = c
                    elif "type" in v:
                        cols["type"] = c
                    elif "affected" in v:
                        cols["jobs"] = c
                    elif "schedule" in v:
                        cols["effective"] = c
                break
        if header_row is None or "date" not in cols or "company" not in cols:
            logger.warning("WI %s: header row not found; layout changed?", year)
            continue

        n = 0
        for r in range(header_row + 1, sheet.nrows):
            raw_date = sheet.cell_value(r, cols["date"])
            if not isinstance(raw_date, float):  # continuation/address rows
                continue
            company = str(sheet.cell_value(r, cols["company"])).strip()
            if not company or company.startswith("<"):
                continue
            notice_date = xlrd.xldate_as_datetime(raw_date, wb.datemode).date()
            eff = sheet.cell_value(r, cols["effective"]) if "effective" in cols else None
            effective = (
                xlrd.xldate_as_datetime(eff, wb.datemode).date().isoformat()
                if isinstance(eff, float) and eff > 10000
                else None
            )
            jobs = sheet.cell_value(r, cols["jobs"]) if "jobs" in cols else None
            notice_type = (
                str(sheet.cell_value(r, cols["type"])).lower() if "type" in cols else ""
            )
            layoff_type = next(
                (v for k, v in _WI_TYPE.items() if k in notice_type), "unknown"
            )
            raw = {
                "year_file": year,
                "Notice Received": notice_date.isoformat(),
                "Company": company,
                "Location": sheet.cell_value(r, cols.get("location", cols["company"])),
                "Type of Notice": notice_type,
                "# Affected": jobs,
                "Schedule of Dislocations": str(eff),
            }
            records.append(
                _canonical(
                    {
                        "state": "WI",
                        "employer_name": company,
                        "location": str(sheet.cell_value(r, cols["location"])).strip()
                        if "location" in cols
                        else None,
                        "notice_date": notice_date.isoformat(),
                        "effective_date": effective,
                        "employees_affected": int(jobs)
                        if isinstance(jobs, float) and jobs > 0
                        else None,
                        "layoff_type": layoff_type,
                        "is_amendment": int("update" in notice_type),
                        "source_url": url,
                    },
                    raw,
                )
            )
            n += 1
        logger.info("WI %s: %d archive rows", year, n)
    return records


# --- Florida -----------------------------------------------------------------


def _fl_date(value: str) -> str | None:
    value = (value or "").strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def fetch_fl(cache_dir: Path) -> list[dict]:
    records: list[dict] = []
    for year in FL_YEARS:
        if year == 1997:
            captures = _wayback_captures(FL_1997_ORIGINAL)
            url = captures[-1] if captures else None  # oldest = closest to 1997
        else:
            url = FL_URL.format(year=year, year_after=year + 1)
        if url is None:
            logger.warning("FL %s: no archived capture found", year)
            continue
        content = _download(url, cache_dir / "archives" / "fl" / f"{year}.html")
        if content is None:
            continue
        soup = BeautifulSoup(content.decode("latin-1", "replace"), "html5lib")
        table = None
        for t in soup.find_all("table"):
            first = t.find("tr")
            if first and "COMPANY NAME" in first.get_text().upper():
                table = t
                break
        if table is None:
            logger.warning("FL %s: no notice table in capture; skipping", year)
            continue

        n = 0
        for row in table.find_all("tr")[1:]:
            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
            if len(cells) < 4 or not cells[0]:
                continue
            notice_date = _fl_date(cells[1])
            if notice_date is None or not notice_date.startswith(str(year)):
                continue  # header repeats / totals / cross-year junk
            jobs = re.sub(r"[^\d]", "", cells[3])
            raw = dict(
                zip(
                    ["COMPANY NAME", "NOTICE DATE", "LAYOFF DATE",
                     "EMPLOYEES AFFECTED", "INDUSTRY"],
                    cells,
                )
            )
            records.append(
                _canonical(
                    {
                        "state": "FL",
                        "employer_name": cells[0],
                        "location": None,
                        "notice_date": notice_date,
                        "effective_date": _fl_date(cells[2]),
                        "employees_affected": int(jobs) if jobs else None,
                        "layoff_type": "unknown",
                        "source_url": url,
                    },
                    raw,
                )
            )
            n += 1
        logger.info("FL %s: %d archive rows", year, n)
    return records


# --- California --------------------------------------------------------------

CA_ORIGINALS = [
    "http://www.edd.ca.gov/Jobs_and_Training/warn/eddwarncn{yy}.pdf",
    "http://www.edd.ca.gov/Jobs_and_Training/warn/eddwarncnal{yy}.pdf",
    "http://www.edd.ca.gov/Jobs_and_Training/warn/eddwarncnda{yy}.pdf",
]
CA_YEARS = range(2000, 2015)
PDF_MAGIC = b"%PDF"


def fetch_ca(cache_dir: Path) -> list[dict]:
    import pdfplumber

    records: list[dict] = []
    for year in CA_YEARS:
        yy = f"{year % 100:02d}"
        content = url = None
        dest = cache_dir / "archives" / "ca" / f"{year}.pdf"
        # The plain, alphabetical, and date-sorted variants hold the same
        # rows; take the first variant with a usable archived PDF.
        for original in CA_ORIGINALS:
            for candidate in _wayback_captures(original.format(yy=yy)):
                content = _download(candidate, dest)
                if content is not None and content.startswith(PDF_MAGIC):
                    url = candidate
                    break
                dest.unlink(missing_ok=True)
                content = None
            if content is not None:
                break
        if content is None:
            logger.warning("CA %s: no usable archived pdf found", year)
            continue

        n = 0
        with pdfplumber.open(dest) as pdf:
            for page in pdf.pages:
                for table in page.find_tables():
                    for cells in table.extract():
                        cells = [" ".join((c or "").split()) for c in cells]
                        if len(cells) < 4 or cells[0] in ("", "Company Name"):
                            continue
                        effective = _fl_date(cells[3])
                        if effective is None:
                            continue
                        jobs = re.sub(r"[^\d]", "", cells[2])
                        records.append(
                            _canonical(
                                {
                                    "state": "CA",
                                    "employer_name": cells[0],
                                    "location": cells[1],
                                    "notice_date": None,
                                    "effective_date": effective,
                                    "employees_affected": int(jobs) if jobs else None,
                                    "layoff_type": "unknown",
                                    "source_url": url,
                                },
                                {
                                    "year_file": year,
                                    "Company Name": cells[0],
                                    "Location": cells[1],
                                    "Employees Affected": cells[2],
                                    "Layoff Date": cells[3],
                                },
                            )
                        )
                        n += 1
        logger.info("CA %s: %d archive rows", year, n)
    return records


FETCHERS = {"WI": fetch_wi, "FL": fetch_fl, "CA": fetch_ca}


def gap_filter(
    conn: sqlite3.Connection, state: str, records: list[dict]
) -> list[dict]:
    """Keep only rows in calendar months where the state has no notices."""
    occupied = {
        r["m"]
        for r in conn.execute(
            "SELECT substr(notice_date, 1, 7) AS m FROM notices "
            "WHERE state = ? AND notice_date IS NOT NULL "
            "UNION SELECT substr(effective_date, 1, 7) FROM notices "
            "WHERE state = ? AND effective_date IS NOT NULL",
            (state, state),
        )
    }
    kept = []
    for rec in records:
        months = {
            d[:7] for d in (rec["notice_date"], rec["effective_date"]) if d
        }
        if months and not (months & occupied):
            kept.append(rec)
    return kept
