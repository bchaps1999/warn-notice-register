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

from warnlive.enrich.industry import _NAICS_SECTORS
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


def _archived(
    original_url: str, dest: Path, valid=None
) -> tuple[bytes | None, str | None]:
    """(bytes, capture URL) for an archived artifact, cache-first.

    A cached artifact is served without touching the network — re-parses
    (see refresh_raw) must not depend on archive.org being reachable. The
    capture URL is cached alongside it, since it is the row's provenance.
    Otherwise captures are tried newest-first until one passes `valid`,
    which rejects Wayback's archived-404 and wrapper pages.
    """
    marker = dest.with_name(dest.name + ".url")
    if dest.exists() and marker.exists():
        content = dest.read_bytes()
        if valid is None or valid(content):
            return content, marker.read_text().strip()
    for candidate in _wayback_captures(original_url):
        content = _download(candidate, dest)
        if content is not None and (valid is None or valid(content)):
            marker.write_text(candidate)
            return content, candidate
        dest.unlink(missing_ok=True)
    return None, None


def _sheet_code(value) -> str | None:
    """Industry code from a spreadsheet cell (floats render as '326199.0')."""
    text = str(int(value)) if isinstance(value, float) and value > 0 else str(value)
    m = re.search(r"\d{2,6}", text)
    return m.group(0) if m else None


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
    rows = None
    for attempt in range(3):  # CDX times out sporadically under load
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                rows = json.loads(resp.read())
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CDX lookup failed for %s (%s)%s",
                original_url, exc, "; retrying" if attempt < 2 else "",
            )
            sleep(5)
    if rows is None:
        return []
    return [
        f"https://web.archive.org/web/{r[1]}id_/{original_url}"
        for r in rows[1:]
    ][::-1]


def fetch_wi(cache_dir: Path) -> list[dict]:
    records: list[dict] = []
    for year in WI_YEARS:
        content, url = _archived(
            WI_ORIGINAL.format(year=year),
            cache_dir / "archives" / "wi" / f"{year}.xls",
            valid=lambda c: c.startswith(XLS_MAGIC),
        )
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
                # Needle order matters twice over: "Schedule of Dislocations"
                # contains "location", so it must be claimed first, and the
                # first column matching a needle keeps it — 2014-2015 logs
                # append an "Industry 2-Digit" column after "Industry".
                for c, v in enumerate(values):
                    for name, needle in (
                        ("date", "notice received"), ("company", "company"),
                        ("effective", "schedule"), ("location", "location"),
                        ("type", "type"), ("jobs", "affected"),
                        ("naics", "naics"), ("industry", "industry"),
                        ("county", "county"),
                    ):
                        if needle in v:
                            cols.setdefault(name, c)
                            break
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
            # The logs carry the state's own industry detail. Through 2001
            # the "NAICS Code" column actually holds pre-conversion SIC
            # codes; the extractor sorts that out by sector validity, so
            # label the column by what it holds, not by its header.
            for name, col in (("Industry", "industry"), ("County", "county")):
                if col in cols:
                    text = str(sheet.cell_value(r, cols[col])).strip()
                    if text:
                        raw[name] = text
            if "naics" in cols:
                code = _sheet_code(sheet.cell_value(r, cols["naics"]))
                if code:
                    raw["NAICS Code" if code[:2] in _NAICS_SECTORS else "SIC Code"] = code
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
            urls = _wayback_captures(FL_1997_ORIGINAL)[::-1]  # oldest first
        else:
            urls = [FL_URL.format(year=year, year_after=year + 1)]
        content = url = None
        dest = cache_dir / "archives" / "fl" / f"{year}.html"
        for candidate in urls:
            content = _download(candidate, dest)
            if content is not None and b"COMPANY NAME" in content.upper():
                url = candidate
                break
            dest.unlink(missing_ok=True)  # capture without the table
            content = None
        if content is None:
            logger.warning("FL %s: no usable archived capture", year)
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


# --- Massachusetts -----------------------------------------------------------

# The only pre-2021 cumulative artifact Wayback holds: FY2020 (Jul 2019 -
# Jun 2020), six regional sheets. Jul 2020 - Mar 2021 has no archived
# artifact anywhere (weekly-report captures only begin late 2021).
MA_FY2020_URL = (
    "https://web.archive.org/web/20200828043125id_/"
    "https://www.mass.gov/doc/warn-report-for-fy-2020/download"
)


def fetch_ma(cache_dir: Path) -> list[dict]:
    content = _download(MA_FY2020_URL, cache_dir / "archives" / "ma" / "fy2020.xls")
    if content is None or not content.startswith(XLS_MAGIC):
        logger.warning("MA: FY2020 capture unavailable or not an xls")
        return []
    wb = xlrd.open_workbook(file_contents=content)
    records: list[dict] = []

    def cell_date(value) -> str | None:
        if isinstance(value, float) and value > 10000:
            return xlrd.xldate_as_datetime(value, wb.datemode).date().isoformat()
        token = str(value).strip().split()[0].rstrip("/") if str(value).strip() else ""
        return _fl_date(token)

    for sheet in wb.sheets():
        for r in range(sheet.nrows):
            row = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            notice_date = cell_date(row[0])
            company = str(row[1]).strip()
            if notice_date is None or not company or company == "Company Name":
                continue
            jobs = re.sub(r"[^\d]", "", str(row[4]).split(".")[0])
            records.append(
                _canonical(
                    {
                        "state": "MA",
                        "employer_name": company,
                        "location": str(row[2]).strip(),
                        "notice_date": notice_date,
                        "effective_date": cell_date(row[3]),
                        "employees_affected": int(jobs) if jobs else None,
                        "layoff_type": "unknown",
                        "is_amendment": int(company.upper().startswith("UPDATED")),
                        "source_url": MA_FY2020_URL,
                    },
                    {
                        "region_sheet": sheet.name,
                        "Date Received": str(row[0]),
                        "Company Name": company,
                        "City": str(row[2]),
                        "Layoff Date": str(row[3]),
                        "# Affected": str(row[4]),
                    },
                )
            )
    logger.info("MA FY2020: %d archive rows", len(records))
    return records


# --- New York ----------------------------------------------------------------

# The pre-Tableau app served one page per notice (details.asp?id=N); Wayback
# crawled it heavily in 2009-2015. Only captures crawled before 2016 are
# fetched: later crawls hold notices the live data already covers, and ~1.8k
# pages at 1/s is already a long polite crawl of archive.org.
NY_DETAILS_PREFIX = "labor.ny.gov/app/warn/details.asp"
NY_CAPTURE_CUTOFF = "2016"

_NY_FIELD = re.compile(
    r"^(Date of Notice|Control Number|Company|County|Number Affected|"
    r"Layoff Date|Closing Date|Classification)\s*:\s*(.*)$"
)


def fetch_ny(cache_dir: Path) -> list[dict]:
    import urllib.parse
    import urllib.request

    query = urllib.parse.urlencode(
        {"url": f"{NY_DETAILS_PREFIX}*", "output": "json",
         "filter": "statuscode:200", "collapse": "urlkey"}
    )
    req = urllib.request.Request(
        f"{CDX_API}?{query}", headers={"User-Agent": "warn-live archive backfill"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            rows = json.loads(resp.read())[1:]
    except Exception as exc:  # noqa: BLE001
        logger.warning("NY: CDX enumeration failed (%s)", exc)
        return []

    captures: dict[str, str] = {}
    for r in rows:
        ts, original = r[1], r[2]
        if ts >= NY_CAPTURE_CUTOFF:
            continue
        m = re.search(r"[?&]id=(\d+)", original)
        if m:
            captures.setdefault(m.group(1), f"https://web.archive.org/web/{ts}id_/{original}")
    logger.info("NY: %d archived detail pages to fetch", len(captures))

    records: list[dict] = []
    parsed = 0
    for notice_id, url in sorted(captures.items(), key=lambda kv: int(kv[0])):
        content = _download(url, cache_dir / "archives" / "ny" / f"{notice_id}.html")
        if content is None:
            continue
        soup = BeautifulSoup(content.decode("utf-8", "replace"), "html5lib")
        fields: dict[str, str] = {}
        lines = soup.get_text("\n", strip=True).split("\n")
        for i, line in enumerate(lines):
            m = _NY_FIELD.match(line)
            if m and m.group(1) not in fields:
                fields[m.group(1)] = m.group(2).strip()
                # Company name may sit on the line after the bare label
                if m.group(1) == "Company" and not m.group(2).strip() and i + 1 < len(lines):
                    fields["Company"] = lines[i + 1].strip()
        company = fields.get("Company", "")
        notice_date = _fl_date(fields.get("Date of Notice", ""))
        if not company or notice_date is None:
            continue
        county = (fields.get("County") or "").split("|")[0].strip()
        classification = (fields.get("Classification") or "").lower()
        # Real counts lead the field ("620 (600 Station Agent...)", "Four
        # (4) will be separated..."); prose buries numbers that are NOT
        # counts (dates, the 250-person statutory threshold). So: strip
        # dates, collapse whitespace, and only accept a number appearing in
        # the first 15 characters.
        jobs_text = " ".join(
            re.sub(
                r"\d{1,2}/\d{1,2}/\d{2,4}", " ",
                fields.get("Number Affected", "").replace(",", ""),
            ).split()
        )
        jobs_m = re.search(r"\d{1,6}", jobs_text[:15])
        jobs = jobs_m.group(0) if jobs_m else ""
        records.append(
            _canonical(
                {
                    "state": "NY",
                    "employer_name": company,
                    "location": county or None,
                    "notice_date": notice_date,
                    "effective_date": _fl_date(fields.get("Layoff Date", ""))
                    or _fl_date(fields.get("Closing Date", "")),
                    "employees_affected": int(jobs) if jobs else None,
                    "layoff_type": "closure" if "closing" in classification
                    else "mass_layoff" if "layoff" in classification else "unknown",
                    "source_url": url,
                    "source_notice_id": fields.get("Control Number") or notice_id,
                },
                dict(fields, wayback_id=notice_id),
            )
        )
        parsed += 1
        if parsed % 200 == 0:
            logger.info("NY: parsed %d detail pages", parsed)
    logger.info("NY: %d archive rows", len(records))
    return records


# --- Ohio ---------------------------------------------------------------------
#
# ODJFS published one document per year from 2001 and kept renaming it
# (WARN_2001.pdf, Warn_2004.pdf, Warn2007.pdf, 2011WARNNotices.pdf,
# WARN2014.stm — the .stm ones are PDFs too). None of it survives on
# jfs.ohio.gov today, which 404s every one of those paths.
#
# The documents print the same eight columns every year but ODJFS changed
# how it produced them, and no single geometry reads all fourteen. Three do,
# and each year is read by whichever one fits it:
#
#   ruled  — 2005-2008 and 2012-2014 are Word tables with real cell borders.
#            Extracting through the borders rejoins wrapped cells for free.
#   banded — 2011 draws no cell borders but rules a full-width line between
#            records, and underlines each header label. The rules group the
#            rows; the underlines give the columns.
#   flowed — 2001-2004 and 2010 are plain text in columns, with no rules at
#            all. Records are anchored on the line carrying the date.
#
# Two traps are common to all three. Header labels are centred over columns
# whose data is left-aligned, so header positions mislocate every field
# (which is why "flowed" learns columns from a histogram of where the data
# starts, and why the header underlines are only trusted when they win on
# quality). And the character spacing is irregular enough that any
# gap-based split cuts words in half ("Deve lopment Grou p"), so fields are
# filled from whole words, never from cell text.
#
# Strategies are scored against the number of distinct WARN IDs the document
# prints — an independent count of how many records it holds — and the best
# one wins. A year whose best parse does not clear _OH_BAR is skipped rather
# than ingested: a mangled employer name is worse than an absent one.

OH_YEARS = range(2001, 2015)  # 2015 onward comes from the live portal
OH_PATTERNS = [
    "http://jfs.ohio.gov/warn/WARN_{year}.pdf",
    "http://jfs.ohio.gov/warn/Warn_{year}.pdf",
    "http://jfs.ohio.gov/warn/pdf/Warn{year}.pdf",
    "http://jfs.ohio.gov/warn/pdf/{year}WARNNotices.pdf",
    "http://jfs.ohio.gov/warn/{year}WARNNotices.stm",
    "http://jfs.ohio.gov/warn/WARN_{year}.stm",
    "http://jfs.ohio.gov/warn/WARN{year}.stm",
]
PDF_MAGIC = b"%PDF"
_OH_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{2,4}$")
_OH_WARN_ID = re.compile(r"^\d{1,3}[‐-]\d{2}[‐-]\d{3}$")
_OH_INT = re.compile(r"^\d[\d,]{0,5}$")
_OH_WARN_ID_ANY = re.compile(r"\b\d{1,3}[‐-]\d{2}[‐-]\d{3}\b")
# A year is ingested only if it parses this well. Coverage catches rows lost
# to bad line grouping; the rate catches unparsed rows; the company-length
# window catches a column split mid-name (too short) or two fields merged
# into one (too long).
_OH_BAR = {"coverage": 0.80, "rate": 0.85, "company_min": 8, "company_max": 42}
_OH_COLUMN_MIN_WIDTH = 34  # narrower than this is a split field, not a column
_OH_RULE_SPAN = 0.4  # a rect this share of the page wide separates records


def _oh_lines(page) -> list[list[dict]]:
    """Words grouped into visual lines, each sorted left to right."""
    lines: dict[int, list] = {}
    for word in page.extract_words():
        lines.setdefault(round(word["top"] / 3), []).append(word)
    return [sorted(ws, key=lambda w: w["x0"]) for _, ws in sorted(lines.items())]


def _oh_columns(all_lines: list[list[dict]]) -> list[float]:
    """Column left edges, learned from where words actually start."""
    from collections import Counter

    data = [ws for ws in all_lines if ws and _OH_DATE.match(ws[0]["text"])]
    if not data:
        return []
    hist: Counter = Counter()
    for ws in data:
        for word in ws:
            hist[round(word["x0"] / 2) * 2] += 1
    floor = max(3, int(len(data) * 0.15))
    edges: list[float] = []
    for x in sorted(x for x, n in hist.items() if n >= floor):
        if not edges or x - edges[-1] >= _OH_COLUMN_MIN_WIDTH:
            edges.append(x)

    def median(values):
        values = sorted(values)
        return values[len(values) // 2] if values else None

    # The company column is sometimes centred, leaving no peak of its own —
    # then the date and the company share column 0. Its left edge shows up
    # either as the first non-date word on a row, or, where the name is too
    # long to fit and gets printed on its own line above the row, as the
    # first word of those lines. Take the leftmost of the two.
    on_row = median([
        min(w["x0"] for w in ws[1:] if not _OH_DATE.match(w["text"]))
        for ws in data
        if len(ws) > 1 and any(not _OH_DATE.match(w["text"]) for w in ws[1:])
    ])
    alone = median([
        ws[0]["x0"] for ws in all_lines
        if ws and not _OH_DATE.match(ws[0]["text"]) and len(ws) <= 6
    ])
    candidates = [x for x in (on_row, alone) if x is not None and x > edges[0] + 20]
    if candidates and not any(abs(min(candidates) - e) <= 12 for e in edges):
        edges = sorted(edges + [round(min(candidates))])
    return edges


def _oh_split(words, edges) -> list[str]:
    cols: list[list[str]] = [[] for _ in edges]
    for word in words:
        idx = 0
        for i, edge in enumerate(edges):
            if word["x0"] >= edge - 3:
                idx = i
        cols[idx].append(word["text"])
    return [" ".join(c).strip() for c in cols]


def _oh_label_columns(rows: list[list[str]]) -> dict | None:
    """Which column holds what, decided by the values rather than a header."""
    width = max((len(r) for r in rows), default=0)
    if width < 4:
        return None

    def share(i, pattern):
        values = [r[i] for r in rows if i < len(r) and r[i]]
        return sum(1 for v in values if pattern.match(v)) / len(values) if values else 0

    jobs = next((i for i in range(1, width) if share(i, _OH_INT) > 0.6), None)
    if jobs is None:
        return None
    warn_id = next(
        (i for i in range(width - 1, 0, -1) if share(i, _OH_WARN_ID) > 0.5), None
    )
    layoff = next(
        (i for i in range(jobs + 1, width) if share(i, _OH_DATE) > 0.5), None
    )
    return {
        "received": 0, "company": 1,
        # City is the column before the count — unless that is the company
        # column itself, in which case this year never separated them and
        # inventing a city would only duplicate the name.
        "city": jobs - 1 if jobs - 1 > 1 else None,
        "jobs": jobs, "layoff": layoff, "warn_id": warn_id,
    }


def _oh_starts_dated(row: list[str]) -> bool:
    """A row of cells whose first field carries a received date.

    The date need not lead the cell: where a record's text is wrapped and
    vertically centred, a fragment printed above the date line sorts ahead
    of it. The received column holds exactly one date, so finding it
    anywhere in the cell is enough to know this is a record.
    """
    return _oh_date(row[0] if row else "") is not None


def _oh_rows_ruled(pdf) -> list[list[str]]:
    """Rows read through the table's own cell borders.

    Where the document draws its cells, the borders already say which text
    belongs to which record — so a name wrapped over three lines arrives
    whole, and the vertically centred layout the later years use costs
    nothing.
    """
    rows = []
    for page in pdf.pages:
        for table in page.find_tables():
            for row in table.extract():
                cells = [(c or "").replace("\n", " ").strip() for c in row]
                if len(cells) >= 6 and _oh_starts_dated(cells):
                    rows.append(cells)
    return rows


def _oh_header_columns(page) -> list[float] | None:
    """Column edges from the underline drawn beneath each header label.

    Unlike the label text, which is centred, an underline spans its column,
    so the midpoint between two of them is a real boundary.
    """
    groups: dict[int, list] = {}
    for rect in page.rects:
        if rect["x1"] - rect["x0"] < page.width * _OH_RULE_SPAN:
            groups.setdefault(round(rect["top"]), []).append(rect)
    row = max(groups.values(), key=len, default=[])
    if len(row) < 6:
        return None
    row = sorted(row, key=lambda r: r["x0"])
    return [row[0]["x0"]] + [(a["x1"] + b["x0"]) / 2 for a, b in zip(row, row[1:])]


def _oh_rows_banded(pdf, edges: list[float]) -> list[list[str]]:
    """Rows separated by the full-width rule the document draws between them.

    The rule is the record boundary the "flowed" reading has to guess at, so
    a name printed above its own row needs no special case: it simply falls
    inside the same band.
    """
    rows = []
    for page in pdf.pages:
        rules = sorted({
            round(rect["top"], 1) for rect in page.rects
            if rect["x1"] - rect["x0"] > page.width * _OH_RULE_SPAN
        })
        if len(rules) < 3:
            continue
        words = page.extract_words()
        for top, bottom in zip(rules, rules[1:] + [page.height]):
            band = [w for w in words if top <= w["top"] < bottom]
            if not band:
                continue
            band.sort(key=lambda w: (round(w["top"] / 3), w["x0"]))
            cells = _oh_split(band, edges)
            if _oh_starts_dated(cells):
                rows.append(cells)
    return rows


def _oh_rows_flowed(pages: list[list[list[dict]]], edges: list[float]) -> list[list[str]]:
    """Rows anchored on the line carrying the received date.

    With no rules to go by, a record is the date line plus the lines that
    continue it. A line holding nothing but a company name belongs to the
    record *below* — that is how these years print a name too long to fit —
    while anything else continues the record above.
    """
    rows: list[list[str]] = []
    for page in pages:
        current, pending = None, []
        for ws in page:
            cols = _oh_split(ws, edges)
            if ws and _OH_DATE.match(ws[0]["text"]):
                if current:
                    rows.append(current)
                current = cols
                if pending and len(current) > 1:
                    current[1] = " ".join(pending + [current[1]]).strip()
                    pending = []
            elif any(cols):
                company_only = len(cols) > 1 and cols[1] and not any(
                    c for i, c in enumerate(cols) if i != 1
                )
                if company_only:
                    pending.append(cols[1])
                elif current:
                    current = [f"{a} {b}".strip() for a, b in zip(current, cols)]
        if current:
            rows.append(current)
    return [r for r in rows if r and _OH_DATE.match(r[0])]


def _oh_readings(path: Path) -> tuple[list[tuple[str, list[list[str]]]], int]:
    """Every way of reading the document, and how many records it holds.

    The count is the number of distinct WARN IDs printed anywhere in the
    text — the document's own record identifiers, so it is independent of
    any parse. The earliest years predate the identifier, and there the
    date-leading line count is the better denominator; whichever is larger
    is the one that has actually seen every record.
    """
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        pages = [_oh_lines(page) for page in pdf.pages]
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
        date_lines = sum(
            1 for page in pages for ws in page if ws and _OH_DATE.match(ws[0]["text"])
        )
        identifiers = len(set(_OH_WARN_ID_ANY.findall(text)))
        expected = identifiers if identifiers >= date_lines * 0.5 else date_lines
        edges = _oh_columns([ln for page in pages for ln in page])
        readings = [("ruled", _oh_rows_ruled(pdf))]
        header_edges = _oh_header_columns(pdf.pages[0]) if pdf.pages else None
        if header_edges:
            readings.append(("banded", _oh_rows_banded(pdf, header_edges)))
        if edges:
            readings.append(("banded-learned", _oh_rows_banded(pdf, edges)))
            readings.append(("flowed", _oh_rows_flowed(pages, edges)))
    return [(name, rows) for name, rows in readings if rows], expected


def _oh_quality(parsed: list[dict], dated_rows: int, expected: int) -> tuple[bool, str]:
    import statistics

    if not parsed or not expected:
        return False, "nothing parsed"
    coverage = dated_rows / expected
    rate = len(parsed) / dated_rows if dated_rows else 0
    median_len = statistics.median(len(p["employer_name"]) for p in parsed)
    detail = (
        f"{dated_rows}/{expected} rows ({coverage:.0%}), {rate:.0%} parsed, "
        f"median name {median_len:.0f} chars"
    )
    ok = (
        coverage >= _OH_BAR["coverage"]
        and rate >= _OH_BAR["rate"]
        and _OH_BAR["company_min"] <= median_len <= _OH_BAR["company_max"]
    )
    return ok, detail


def _oh_count(cell: str) -> int | None:
    """The headcount from a cell the neighbouring column has bled into.

    Where the layoff-date text wraps, its first word lands in the count
    column: "124 Begin", "303 3/6/10 Begins". The count is still the first
    thing in its own column, so the leading integer is it — unless the cell
    holds a second bare integer, which means the columns are genuinely
    confused ("2 1 3 2/28/11" is 213 broken up by spacing, not 2) and
    guessing would invent a number the state never printed.
    """
    tokens = (cell or "").split()
    if not tokens or not _OH_INT.match(tokens[0]):
        return None
    if any(_OH_INT.match(t) for t in tokens[1:]):
        return None
    return int(tokens[0].replace(",", ""))


def _oh_build(
    rows: list[list[str]], labels: dict, year: int, url: str | None
) -> list[dict]:
    """Notices from labelled rows; rows missing a name or a count are dropped."""
    def field(row, name):
        idx = labels.get(name)
        return row[idx] if idx is not None and idx < len(row) else ""

    parsed = []
    for row in rows:
        company = _clean_text(field(row, "company"))
        jobs = field(row, "jobs")
        count = _oh_count(jobs)
        if not company or count is None:
            continue
        city = _clean_text(field(row, "city"))
        warn_id = field(row, "warn_id")
        # A yearly document listing a date years outside its own year has a
        # typo in it — the 2001 file prints "02/23/91" against a 2001 layoff
        # date. The printed value stays in raw_extra; the notice falls back
        # to its effective date, as dateless sources do, so one typo cannot
        # stretch the state's coverage by a decade.
        received = _oh_date(field(row, "received"))
        if received and abs(int(received[:4]) - year) > 1:
            received = None
        parsed.append({
            "state": "OH",
            "employer_name": company,
            "location": city,
            "notice_date": received,
            "effective_date": _oh_date(field(row, "layoff")),
            "employees_affected": count,
            "layoff_type": "unknown",
            "source_url": url,
            "source_notice_id": warn_id if _OH_WARN_ID.match(warn_id or "") else None,
            "_raw": {
                "year_file": year, "Date Rec'd": field(row, "received"),
                "Company": company, "City": city or "",
                "# Affected": jobs, "Layoff Date": field(row, "layoff"),
                "WARN ID": warn_id,
            },
        })
    return parsed


def fetch_oh(cache_dir: Path) -> list[dict]:
    records: list[dict] = []
    for year in OH_YEARS:
        dest = cache_dir / "archives" / "oh" / f"{year}.pdf"
        content = url = None
        for pattern in OH_PATTERNS:
            content, url = _archived(
                pattern.format(year=year), dest, valid=lambda c: c.startswith(PDF_MAGIC)
            )
            if content is not None:
                break
        if content is None:
            logger.warning("OH %s: no archived yearly document found", year)
            continue

        readings, expected = _oh_readings(dest)
        best: tuple[str, list[dict], str] | None = None
        rejected: list[str] = []
        for name, rows in readings:
            labels = _oh_label_columns(rows)
            if labels is None:
                rejected.append(f"{name}: columns unidentifiable")
                continue
            parsed = _oh_build(rows, labels, year, url)
            ok, detail = _oh_quality(parsed, len(rows), expected)
            if not ok:
                rejected.append(f"{name}: {detail}")
            elif best is None or len(parsed) > len(best[1]):
                best = (name, parsed, detail)
        if best is None:
            logger.warning(
                "OH %s: no reading cleared the bar (%s); skipped",
                year, "; ".join(rejected) or "nothing parsed",
            )
            continue
        logger.info("OH %s: %s via %s — %d rows", year, best[2], best[0], len(best[1]))
        for rec in best[1]:
            records.append(_canonical(rec, rec.pop("_raw")))
    logger.info("OH: %d archive rows", len(records))
    return records


def _oh_date(value: str) -> str | None:
    """The first date in a cell; Ohio prints m/d/yy and m/d/yyyy alike.

    Scanning rather than reading the leading token matters because the
    neighbouring column bleeds words in: a layoff date is printed as
    "Begins 2/12/10 until 3/31/10", and a received date can sit behind a
    wrapped fragment that sorts ahead of it.
    """
    for token in (value or "").split():
        for fmt in ("%m/%d/%Y", "%m/%d/%y"):
            try:
                return datetime.strptime(token, fmt).date().isoformat()
            except ValueError:
                continue
    return None


FETCHERS = {"WI": fetch_wi, "FL": fetch_fl, "CA": fetch_ca, "MA": fetch_ma,
            "NY": fetch_ny, "OH": fetch_oh}


def refresh_raw(conn: sqlite3.Connection, records: list[dict]) -> dict:
    """Rewrite already-ingested rows' raw_extra from a re-parse.

    When a parser learns to read columns it previously dropped (WI's
    Industry/NAICS), the canonical fields are unchanged, so the version
    hash — which covers only those fields — matches and a plain re-ingest
    reports "unchanged" without storing the new detail. This updates
    raw_extra on the current version in place: no version bump, and no
    is_amended flag, because the source never amended anything.
    """
    updated = missing = 0
    for rec in records:
        row = conn.execute(
            "SELECT v.rowid AS rowid, v.fields_json AS fields_json "
            "FROM notices n JOIN notice_versions v "
            "  ON v.notice_id = n.id AND v.version = n.current_version "
            "WHERE n.dedupe_key = ?",
            (rec["dedupe_key"],),
        ).fetchone()
        if row is None:
            missing += 1
            continue
        fields = json.loads(row["fields_json"])
        if fields.get("raw_extra") == rec["raw_extra"]:
            continue
        fields["raw_extra"] = rec["raw_extra"]
        conn.execute(
            "UPDATE notice_versions SET fields_json = ? WHERE rowid = ?",
            (json.dumps(fields, sort_keys=True, ensure_ascii=False), row["rowid"]),
        )
        updated += 1
    conn.commit()
    return {"updated": updated, "not_in_db": missing}


def drop_archive_rows(conn: sqlite3.Connection, state: str) -> int:
    """Delete a state's archive-backfilled rows (Wayback source_url only).

    For when a parser bug — not the source — produced bad rows: WI's
    "Schedule of Dislocations" column matched a substring test for
    "location", so its rows carried a date serial as their location and
    the bad value went into their dedupe keys. Rows keyed on garbage can't
    be matched and repaired in place, so they are dropped and re-ingested
    from the cached artifacts. Live-scraped rows are never touched.
    """
    ids = [
        r["id"]
        for r in conn.execute(
            "SELECT id FROM notices WHERE state = ? "
            "AND source_url LIKE '%web.archive.org%'",
            (state,),
        )
    ]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"DELETE FROM notice_links WHERE notice_id IN ({marks}) "
        f"OR related_id IN ({marks})",
        ids * 2,
    )
    conn.execute(f"DELETE FROM notice_versions WHERE notice_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM notices WHERE id IN ({marks})", ids)
    conn.commit()
    return len(ids)


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
