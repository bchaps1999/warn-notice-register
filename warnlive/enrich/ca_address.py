"""Source-backed California EDD WARN report rows and conservative address matches.

The fiscal-year PDFs and current workbook print one row per affected site.  A
notice may cover several sites, so a shared employer and notice date alone do
not identify an address.  Parsing is offline; callers supply pinned reports.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_STREET_RE = re.compile(r"^\d{1,6}[\w./-]*\s+[A-Za-z]")
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STATE_ZIP = re.compile(r"\b([A-Za-z]{2})\s+\d{5}(?:-\d{4})?\b")
_HEADER = {
    "notice date": "notice_date",
    "received date": "received_date",
    "processed date": "processed_date",
    "effective date": "effective_date",
    "company": "company",
    "county": "county",
    "county parish": "county",
    "no of employees": "workers",
    "address": "address",
}
_REQUIRED = frozenset(("notice_date", "effective_date", "company", "county", "workers", "address"))


@dataclass(frozen=True)
class CaRecord:
    company: str
    notice_date: str
    effective_date: str | None
    address: str
    source_file: str
    received_date: str | None = None
    processed_date: str | None = None
    workers: int | None = None
    county: str | None = None
    source_sha256: str | None = None
    locators: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CaMatch:
    status: str  # matched | unresolved
    reason: str
    record: CaRecord | None = None
    candidates: tuple[CaRecord, ...] = ()


class CaMatcher:
    """Index report rows once when matching a batch of notices."""

    def __init__(self, records: Iterable[CaRecord]) -> None:
        self.by_employer_date: dict[tuple[str, str], list[CaRecord]] = {}
        for row in records:
            self.by_employer_date.setdefault((_name(row.company), row.notice_date), []).append(row)

    def match(self, notice: Mapping[str, object]) -> CaMatch:
        return match_ca_notice(notice, self)


def _cell(value: object) -> str:
    return _WS.sub(" ", html.unescape(str(value or ""))).strip()


def _iso(value: object) -> str | None:
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    raw = _cell(value)
    match = _DATE_RE.fullmatch(raw)
    if match:
        try:
            return dt.date(int(match[3]), int(match[1]), int(match[2])).isoformat()
        except ValueError:
            return None
    try:
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        return None


def _clean(value: object) -> str | None:
    address = _cell(value).strip(" ,;")
    return address if _STREET_RE.search(address) else None


def _header(row: Iterable[object]) -> dict[str, int] | None:
    names = [_NON_ALNUM.sub(" ", _cell(value).casefold()).strip() for value in row]
    columns = {_HEADER[name]: i for i, name in enumerate(names) if name in _HEADER}
    return columns if _REQUIRED <= columns.keys() and ("received_date" in columns or "processed_date" in columns) else None


def _name(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", html.unescape(value or "")).casefold()
    return _NON_ALNUM.sub(" ", raw).strip()


def _county(value: str | None) -> str:
    name = _name(value)
    return re.sub(r"\s+county$", "", name)


def _workers(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    raw = _cell(value).replace(",", "")
    return int(raw) if raw.isdecimal() else None


@lru_cache(maxsize=1)
def _place_resolver():
    # The repository's pinned Census roster is local and read-only. Reuse
    # its address parser rather than inventing a second city/county mapping.
    from warnlive.enrich.places import Resolver

    return Resolver()


def _foreign_address_state(address: str) -> str | None:
    """Spot an explicit non-CA state even when a store label follows the ZIP."""
    from warnlive.enrich.places import _POSTAL, _foreign_state

    for match in _STATE_ZIP.finditer(address):
        state = match[1].upper()
        if state in _POSTAL and state != "CA":
            return state
    return _foreign_state("CA", address)


def _record(row: Iterable[object], columns: Mapping[str, int], path: Path,
            sha256: str, locator: str) -> CaRecord | None:
    cells = list(row)
    if len(cells) <= max(columns.values()):
        raise ValueError(f"{locator}: truncated EDD report row")
    get = lambda key: cells[columns[key]]  # noqa: E731
    notice = _iso(get("notice_date"))
    if not notice:
        # Empty and report summary rows are outside the detailed register.
        first = _cell(get("notice_date"))
        if (not any(_cell(value) for value in cells)
                or not _cell(get("company"))
                or first == "Summary by Month"
                or re.fullmatch(r"[A-Za-z]+ \d{4}", first)):
            return None
        raise ValueError(f"{locator}: invalid notice date")
    company = _cell(get("company"))
    if not company:
        raise ValueError(f"{locator}: missing company")
    received = _iso(get("received_date")) if "received_date" in columns else None
    processed = _iso(get("processed_date")) if "processed_date" in columns else None
    effective = _iso(get("effective_date"))
    workers = _workers(get("workers"))
    county = _cell(get("county"))
    if not (received or processed) or not effective or workers is None or not county:
        raise ValueError(f"{locator}: missing or invalid date, workers, or county")
    address = _clean(get("address"))
    if not address:
        return None  # No site address is asserted by this source row.
    return CaRecord(company, notice, effective, address, path.name,
                    received_date=received, processed_date=processed, workers=workers,
                    county=county, source_sha256=sha256, locators=(locator,))


def parse_pdf(path: Path) -> list[CaRecord]:
    import pdfplumber

    path = Path(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    records: list[CaRecord] = []
    saw_header = False
    with pdfplumber.open(path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            for table_num, table in enumerate(page.extract_tables(), start=1):
                columns = None
                for row_num, row in enumerate(table, start=1):
                    header = _header(row)
                    if header:
                        columns, saw_header = header, True
                        continue
                    # Continuation pages sometimes omit the heading. The seven
                    # fixed fields are used only after a compatible header was
                    # seen and only on an eight-column detailed table.
                    if columns is None and saw_header and len(row) == 8:
                        columns = dict(zip(("notice_date", "received_date", "effective_date",
                                            "company", "county", "workers", "type", "address"),
                                           range(8)))
                    if columns is None or len(row) != 8:
                        continue
                    locator = f"page:{page_num}/table:{table_num}/row:{row_num}"
                    record = _record(row, columns, path, sha256, locator)
                    if record:
                        records.append(record)
    if not saw_header:
        raise ValueError(f"{path.name}: EDD detailed report header not found")
    return records


def parse_xlsx(path: Path) -> list[CaRecord]:
    import openpyxl

    path = Path(path)
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = next((workbook[name] for name in workbook.sheetnames
                      if name.strip() == "Detailed WARN Report"), None)
        if sheet is None:
            raise ValueError(f"{path.name}: Detailed WARN Report sheet not found")
        columns = None
        records = []
        for row_num, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if columns is None:
                columns = _header(row)
                continue
            locator = f"sheet:{sheet.title.strip()}/row:{row_num}"
            record = _record(row, columns, path, sha256, locator)
            if record:
                records.append(record)
        if columns is None:
            raise ValueError(f"{path.name}: EDD detailed report header not found")
        return records
    finally:
        workbook.close()


def collect_records(cache_dir: Path) -> list[CaRecord]:
    """Read available pinned reports from a directory without fetching or writing."""
    cache_dir = Path(cache_dir)
    records = []
    for path in sorted((*cache_dir.glob("*.pdf"), *cache_dir.glob("*.xlsx"))):
        if path.name == "warn_report1.xlsx" or path.name.startswith("warn-report-for-7-1-"):
            parsed = parse_xlsx(path) if path.suffix == ".xlsx" else parse_pdf(path)
            logger.info("ca-address: %s -> %d records", path.name, len(parsed))
            records.extend(parsed)
    return records


def match_ca_notice(notice: Mapping[str, object], records: Iterable[CaRecord] | CaMatcher) -> CaMatch:
    """Resolve one notice to one site only when source discriminators agree."""
    employer = _name(str(notice.get("employer_name") or ""))
    date = _iso(notice.get("notice_date"))
    effective = _iso(notice.get("effective_date"))
    workers = _workers(notice.get("employees_affected"))
    county = _county(str(notice.get("county") or ""))
    if not employer or not date:
        return CaMatch("unresolved", "missing_employer_or_notice_date")
    if not effective or workers is None:
        return CaMatch("unresolved", "missing_effective_date_or_workers")
    candidates = (list(records.by_employer_date.get((employer, date), ()))
                  if isinstance(records, CaMatcher) else
                  [r for r in records if _name(r.company) == employer and r.notice_date == date])
    if not candidates:
        return CaMatch("unresolved", "no_employer_notice_date_match")
    candidates = [r for r in candidates if r.effective_date == effective and r.workers == workers]
    if not candidates:
        return CaMatch("unresolved", "effective_date_or_workers_mismatch")
    if county:
        candidates = [r for r in candidates if _county(r.county) == county]
        if not candidates:
            return CaMatch("unresolved", "county_mismatch")
    # Multiple counties without a notice county remain unresolved even when
    # addresses happen to be identical: the source site is unidentified.
    if not county and len({_county(r.county) for r in candidates}) > 1:
        return CaMatch("unresolved", "missing_county_discriminator", candidates=tuple(candidates))
    addresses = {_name(r.address) for r in candidates}
    if len(addresses) != 1:
        return CaMatch("unresolved", "conflicting_addresses", candidates=tuple(candidates))
    first = candidates[0]
    locators = tuple(dict.fromkeys(locator for r in candidates for locator in r.locators))
    # A report may repeat an identical row in more than one capture. Preserve
    # all locators but do not hide changes in the report file or digest.
    files = {(r.source_file, r.source_sha256) for r in candidates}
    if len(files) != 1:
        return CaMatch("unresolved", "multiple_source_artifacts", candidates=tuple(candidates))
    if len({(r.company, r.notice_date, r.received_date, r.processed_date, r.effective_date,
             r.workers, r.county, r.address) for r in candidates}) != 1:
        return CaMatch("unresolved", "nonidentical_source_rows", candidates=tuple(candidates))
    if _foreign_address_state(first.address):
        return CaMatch("unresolved", "role_conflict", candidates=tuple(candidates))
    resolved_county = _place_resolver().resolve("CA", first.address).get("county_name")
    if resolved_county and _county(resolved_county) != _county(first.county):
        # EDD's Address can be a mailing/office address rather than the site
        # in the County column. A row identity match cannot settle that role.
        return CaMatch("unresolved", "role_conflict", candidates=tuple(candidates))
    matched = CaRecord(first.company, first.notice_date, first.effective_date,
                       first.address, first.source_file, received_date=first.received_date,
                       processed_date=first.processed_date, workers=first.workers,
                       county=first.county, source_sha256=first.source_sha256,
                       locators=locators)
    return CaMatch("matched", "exact_source_row", matched, tuple(candidates))
