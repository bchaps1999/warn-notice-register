"""Read frozen Louisiana WARN tables and project the reviewed rows.

Input: the pinned ``agency/la`` manifest and annual PDFs of a source bundle.
``extract`` returns every table row as evidence; the agency's 2025 layout
combines employer and address text, and its 2026 layout separates them but
does not establish whether every address is a worksite, so both stay
verbatim. ``record`` projects one reviewed notice row for the offline
rebuild, and ``source_exceptions`` lists the annotation, rescinded and held
rows for its exception ledger. The PDF is a table of reported notices, not a
filing archive: two unresolved clusters stay out of active counts, and no
address role is inferred nor a reported worker total split across sites.
``read_archive``/``project_archive`` apply general rules (not a reviewed
mapping) to ``agency/la_archive``, the Wayback captures of the 2007-2024
tables (data/source_snapshots/la/wayback-2026-09-30); rows carrying update or
rescission markers and continuation rows are held. No network access.

In ``project_archive``, dates outside the live transformer's window (``archive_dates``) are blanked
and reported under ``implausible_dates_blanked``.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

import pdfplumber

from warnlive.migrate import archive_dates
from warnlive.normalize.engine import _record_hash

HEADERS = {
    2025: ["Company Name", "Notice\nDate", "Layoff\nDate",
           "Employees\nAffected", "Industry"],
    2026: ["Company Name", "Address", "Notice\nDate", "Layoff\nDate",
           "Employees\nAffected", "Industry"],
}
RANGE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4})\s+to\s+"
                   r"(\d{1,2}/\d{1,2}/\d{2,4})\s*$", re.I)


def _date(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value or value.lower() == "not specified":
        return None
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unexpected Louisiana date: {value!r}")


def _row(year: int, page: int, ordinal: int, cells: list[str | None]) -> dict:
    raw = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
    row = {
        "source_artifact": f"agency/la/{year}.pdf",
        "source_row": f"{year}.pdf:p{page}:r{ordinal}",
        "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "raw_cells": cells,
        "report_year": year,
        "page": page,
        "table_row": ordinal,
    }
    if year == 2025:
        company, notice, layoff, workers, industry = cells
        row["company_and_address_text"] = company
        row["address_text"] = None
    else:
        company, address, notice, layoff, workers, industry = cells
        row["company_text"] = company
        row["address_text"] = address
        row["address_role"] = "unverified"
    if all(cell is None for cell in cells[1:]):
        row.update(kind="annotation", annotation_text=cells[0])
        return row
    if not company or not workers or not workers.isdecimal():
        raise ValueError(f"incomplete Louisiana notice row: {row['source_row']}")
    span = RANGE.fullmatch((layoff or "").replace("\n", " "))
    start = _date(span.group(1)) if span else _date(layoff)
    end = _date(span.group(2)) if span else None
    if start and end and end < start:
        raise ValueError(f"reversed Louisiana date range: {row['source_row']}")
    row.update(
        kind="notice", notice_date=_date((notice or "").replace("\n", " ")),
        notice_date_text=notice, effective_date_start=start,
        effective_date_end=end, effective_date_text=layoff,
        date_interpretation="interval" if span else "single_date",
        workers_total=int(workers), industry_text=industry,
        status="reported", worker_allocation="unverified",
    )
    return row


def extract(directory: Path) -> list[dict]:
    """Verify source bytes and exact layouts, then return every table row."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "warn-source-artifacts-v1":
        raise ValueError("unsupported Louisiana source manifest")
    artifacts = manifest.get("artifacts") or []
    if {item.get("path") for item in artifacts} != {"2025.pdf", "2026.pdf"}:
        raise ValueError("Louisiana source manifest must list both annual PDFs")
    result = []
    for item in sorted(artifacts, key=lambda item: item["path"]):
        year = int(Path(item["path"]).stem)
        path = directory / item["path"]
        content = path.read_bytes()
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Louisiana source checksum mismatch: {path}")
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) != item["pages"]:
                raise ValueError(f"Louisiana page count changed: {path}")
            for page_number, page in enumerate(pdf.pages, start=1):
                table = page.extract_table()
                if not table or table[0] != HEADERS[year]:
                    raise ValueError(f"Louisiana table layout changed: {path} page {page_number}")
                for ordinal, cells in enumerate(table[1:], start=2):
                    row = _row(year, page_number, ordinal, cells)
                    row["source_url"] = item["source_url"]
                    row["source_pdf_sha256"] = item["sha256"]
                    result.append(row)
    annotations = [row for row in result if row["kind"] == "annotation"]
    if len(annotations) != 1 or "Rescinded on 9/5/2025" not in annotations[0]["annotation_text"]:
        raise ValueError("Louisiana rescission annotation changed")
    annotation = annotations[0]
    target = next((row for row in result if row["source_row"] == "2025.pdf:p2:r5"), None)
    if target is None or "(*)UPS" not in target["company_and_address_text"]:
        raise ValueError("Louisiana rescission target changed")
    quote = annotation["annotation_text"]
    match = re.search(r"\bRescinded on (\d{1,2}/\d{1,2}/\d{2,4})\b", quote)
    if match is None:
        raise ValueError("Louisiana rescission date changed")
    rescission_date = _date(match.group(1))
    # This is a status event about a notice. It is neither another notice nor
    # the end of a layoff interval, so keep it in dedicated typed fields.
    target["status"] = "rescinded"
    target["status_source_row"] = annotation["source_row"]
    target["rescission_date"] = rescission_date
    target["rescission_target_source_row"] = target["source_row"]
    target["rescission_source_quote"] = quote
    annotation["applies_to_source_row"] = target["source_row"]
    annotation["rescission_date"] = rescission_date
    annotation["rescission_target_source_row"] = target["source_row"]
    annotation["rescission_source_quote"] = quote
    return result


# Reviewed projection of the pinned table (manifest checksum below).
LA_MANIFEST_SHA256 = "bbdd7a445f406f4f8b8153a4514b8eadf048a8653505b585d62d737e138ef6b7"

HELD = {
    "2025.pdf:p1:r7", "2025.pdf:p1:r8",  # IDEA: possible duplicate filings
    "2025.pdf:p2:r7", "2025.pdf:p2:r11", "2025.pdf:p2:r12",
    "2025.pdf:p2:r13",  # SafeSource: aggregate versus facility phases
}

# A name is a reviewed projection of the PDF company cell.  The PDF address
# remains unaltered in source_details, where its role is explicitly unverified.
EMPLOYERS = {
    "2025.pdf:p1:r2": "Dr. Reddy’s Laboratories",
    "2025.pdf:p1:r3": "International Paper Company",
    "2025.pdf:p1:r4": "Boeing Company",
    "2025.pdf:p1:r5": "General Dynamics Information Technology",
    "2025.pdf:p1:r6": "Southern Glazer's Wine and Spirits of Louisiana",
    "2025.pdf:p1:r9": "Federal Express Corporation (BTRA Facility)",
    "2025.pdf:p1:r10": "Cornerstone Chemical Company",
    "2025.pdf:p1:r11": "Syncom Space Services",
    "2025.pdf:p2:r2": "Sodexo",
    "2025.pdf:p2:r3": "Roux 61",
    "2025.pdf:p2:r4": "Albertsons Baton Rouge #0709",
    "2025.pdf:p2:r8": "Smitty’s Supply Inc",
    "2025.pdf:p2:r9": "PosiGen Developer LLC",
    "2025.pdf:p2:r10": "Premier Health Consultants, LLC",
    "2025.pdf:p2:r14": "Ensco Offshore LLC",
    "2025.pdf:p2:r15": "The Service Companies, Inc.",
    "2025.pdf:p2:r16": "General Dynamics Information Technology (GDIT)",
    "2026.pdf:p1:r2": "Westlake Corporation",
    "2026.pdf:p1:r3": "McGlinchey Stafford PLLC",
    "2026.pdf:p1:r4": "Denka Performance Elastomer LLC",
    "2026.pdf:p1:r5": "Stockhausen Superabsorber LLC",
    "2026.pdf:p1:r6": "C2 Technologies",
    "2026.pdf:p1:r7": "C&S Wholesale Services LLC",
    "2026.pdf:p1:r8": "Einstein Charter Schools",
    "2026.pdf:p1:r9": "Republic National Distributing Co",
    "2026.pdf:p1:r10": "Republic National Distributing Co",
    "2026.pdf:p1:r11": "Hydro Extrusion USA LLC",
    "2026.pdf:p1:r12": "Conduent Commercial Solutions",
    "2026.pdf:p1:r13": "United Parcel Service",
    "2026.pdf:p1:r14": "Mosaic Company",
    "2026.pdf:p1:r15": "Elevance Health, Inc.",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source_rows(rows: list[dict], la_dir: Path) -> None:
    """Pin the reviewed agency table the projection below was reviewed against."""
    if _sha(la_dir / "manifest.json") != LA_MANIFEST_SHA256:
        raise ValueError("Louisiana reviewed source manifest drift")
    notices = {row["source_row"]: row for row in rows if row["kind"] == "notice"}
    if (len(rows) != 39 or len(notices) != 38
            or set(notices) != set(EMPLOYERS) | HELD | {"2025.pdf:p2:r5"}):
        raise ValueError("Louisiana reviewed source rows drift")


def record(row: dict) -> dict:
    """Project one accepted table row, retaining ambiguity as evidence."""
    source_row = row["source_row"]
    if source_row not in EMPLOYERS:
        raise ValueError(f"Louisiana source row is not accepted: {source_row}")
    details = {
        "origin": row["source_artifact"],
        "source_row": source_row,
        "source_row_sha256": row["source_row_sha256"],
        "source_pdf_sha256": row["source_pdf_sha256"],
        "raw_cells": row["raw_cells"],
        "address_role": "unverified",
        "address_text": row.get("address_text"),
        "company_and_address_text": row.get("company_and_address_text"),
        "worker_allocation": "unresolved",
        "date_interpretation": row["date_interpretation"],
    }
    if source_row == "2026.pdf:p1:r14":
        # The table names two facilities but gives only one 206-worker total.
        details["sites"] = [
            {"address_text": "Uncle Sam - 7250 LA-44, Convent, LA 70723",
             "address_role": "unverified", "workers": None},
            {"address_text": "Faustina – 9959 LA-18, St. James, LA 70086",
             "address_role": "unverified", "workers": None},
        ]
    rec = {
        "state": "LA", "employer_name": EMPLOYERS[source_row],
        "location": None, "notice_date": row["notice_date"],
        "effective_date": row["effective_date_start"],
        "effective_date_end": row["effective_date_end"],
        "employees_affected": row["workers_total"],
        "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
        "source_url": row["source_url"], "source_notice_id": source_row,
        "source_identity": f"LA:official:{source_row}",
        "source_details": json.dumps(details, sort_keys=True, ensure_ascii=False),
        "raw_extra": json.dumps(row, sort_keys=True, ensure_ascii=False),
        "dedupe_key": hashlib.sha1(f"LA|official|{source_row}".encode()).hexdigest(),
    }
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def source_exceptions(rows: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        if row["kind"] == "annotation":
            reason = "official_source_annotation"
        elif row["source_row"] in HELD:
            reason = "official_source_held_for_review"
        elif row["status"] == "rescinded":
            reason = "official_source_rescinded"
        else:
            continue
        result.append({
            "origin": row["source_artifact"], "state": "LA", "reason": reason,
            "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
            "source_url": row["source_url"],
            "raw_extra": json.dumps(row, sort_keys=True, ensure_ascii=False),
        })
    return result


# ---------------------------------------------------------------------------
# Louisiana Wayback archive (agency/la_archive): the agency's annual
# WarnNotices{YYYY}.pdf tables for 2007-2024 as archived by the Wayback
# Machine (data/source_snapshots/la/wayback-2026-09-30). The live URLs return
# 404. The 2024 capture is from August 2024 (partial year) and the 2019
# capture from 2019-12-22.

ARCHIVE_PREFIX = "agency/la_archive"
ARCHIVE_FORMAT = "la-wayback-archive-v1"
ARCHIVE_HEADER = ["CompanyName", "NoticeDate", "LayoffDate", "EmployeesAffected", "Industry"]
DAY = re.compile(r"\d{1,2}/\d{1,2}/(?:\d{2}|\d{4})")
SPAN = re.compile(r"(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))\s*(?:[-–]|to)\s*(\d{1,2}/\d{1,2}/(?:\d{4}|\d{2}))",
                  re.I)
ADDRESS_LINE = re.compile(
    r"^\s*(?:\d|P\.?\s*O\.?\s*Box\b|PO Box\b|One\s|Suite\b|Ste\.?\s)"
    r"|,\s*(?:LA|Louisiana)\b|\b[A-Z]{2}\s*\d{5}\b|\bLA\d{5}\b", re.I)
MARKER_LINE = re.compile(r"\bupdat|\brescind|\bWARN\s+Rescinded", re.I)


def _archive_day(value: str) -> str | None:
    value = " ".join((value or "").split())
    if not DAY.fullmatch(value):
        return None
    try:
        return _date(value)
    except ValueError:
        return None


def _archive_span(value: str) -> tuple[str | None, str | None]:
    text = " ".join((value or "").split())
    single = _archive_day(text)
    if single:
        return single, None
    match = SPAN.fullmatch(text)
    if not match:
        return None, None
    start, end = _archive_day(match.group(1)), _archive_day(match.group(2))
    if not start or not end or end < start:
        return None, None
    return start, end


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())[:12]


def split_company_cell(cell: str) -> tuple[str, str | None]:
    """The employer lines before the first address or status line, and the rest."""
    lines = [" ".join(line.split()) for line in (cell or "").split("\n")]
    lines = [line for line in lines if line]
    name: list[str] = []
    for index, line in enumerate(lines):
        if name and (ADDRESS_LINE.search(line) or MARKER_LINE.search(line)):
            rest = "\n".join(lines[index:])
            return " ".join(name), rest or None
        name.append(line)
    return " ".join(name), None


def read_archive(directory: Path) -> list[dict]:
    """Verify every pinned annual PDF and return each non-empty table row."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = manifest.get("artifacts") or []
    if (manifest.get("format") != ARCHIVE_FORMAT
            or sorted(item.get("year") for item in specs) != list(range(2007, 2025))):
        raise ValueError("unsupported Louisiana archive manifest")
    rows = []
    for spec in sorted(specs, key=lambda item: item["year"]):
        name = spec["file"]
        path = directory / name
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"WarnNotices\d{4}-\d{14}\.pdf", name):
            raise ValueError(f"invalid Louisiana archive artifact: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != spec["bytes"] or digest != spec["sha256"]:
            raise ValueError(f"Louisiana archive checksum mismatch: {name}")
        count = 0
        with pdfplumber.open(path) as pdf:
            if len(pdf.pages) != spec["pages"]:
                raise ValueError(f"Louisiana archive page count changed: {name}")
            for page_number, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables():
                    for ordinal, cells in enumerate(table, start=1):
                        cells = [cell or "" for cell in cells]
                        if len(cells) != 5:
                            raise ValueError(f"Louisiana archive row width changed: {name}:{page_number}")
                        if ["".join(cell.split()) for cell in cells] == ARCHIVE_HEADER:
                            continue
                        if not any(cell.strip() for cell in cells):
                            continue
                        count += 1
                        raw = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
                        rows.append({
                            "year": spec["year"], "file": name, "page": page_number,
                            "table_row": ordinal, "cells": cells,
                            "source_artifact": f"{ARCHIVE_PREFIX}/{name}",
                            "source_pdf_sha256": digest,
                            "source_row": (f"{ARCHIVE_PREFIX}/{name}:sha256:{digest}:"
                                           f"page:{page_number}:table_row:{ordinal}"),
                            "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                            "source_url": spec["wayback_url"],
                        })
        if count != spec["data_rows"]:
            raise ValueError(f"Louisiana archive row count changed: {name}")
    return rows


def _json_cells(row: dict) -> str:
    return json.dumps([row["file"], row["cells"]], ensure_ascii=False)


def existing_events(rows: list[dict]) -> set[tuple]:
    """(notice date, workers, employer key) of every agency/la table notice row."""
    events = set()
    for row in rows:
        if row.get("kind") != "notice":
            continue
        company = row.get("company_text") or (row.get("company_and_address_text") or "").split("\n")[0]
        events.add((row.get("notice_date"), row.get("workers_total"), _name_key(company)))
    return events


def project_archive(directory: Path, known_events: set[tuple] | None = None
                    ) -> tuple[list[dict], list[dict], dict]:
    """Admit complete archive rows; hold status, continuation and repeated rows.

    A row carrying an update or rescission marker is held: the tables stack
    later filings under the original without saying which values changed.
    A row without its own employer or notice cell continues an earlier row
    (a phase or wrapped address) and is held with a pointer to that row.
    """
    rows = read_archive(directory)
    known_events = set(known_events or ())
    records, held = [], []
    admitted_events: dict[tuple, str] = {}
    admitted_cells: dict[str, str] = {}
    previous = None
    for row in rows:
        company_cell, notice_cell, layoff_cell, workers_cell, industry = row["cells"]
        text = " ".join(row["cells"])
        name, rest = split_company_cell(company_cell)
        notice = _archive_day(notice_cell)
        workers_text = " ".join(workers_cell.split()).replace(",", "")
        workers = int(workers_text) if workers_text.isdigit() and int(workers_text) > 0 else None
        event = (notice, workers, _name_key(name))
        site_event = (*event, re.sub(r"[^a-z0-9]", "", (rest or "").casefold()))
        related = None
        if not company_cell.strip() or not any(c.strip() for c in row["cells"][1:]):
            reason, related = "official_source_continuation_row", previous
        elif re.search(r"\brescind", text, re.I):
            reason = "official_source_rescinded"
        elif re.search(r"\bupdat", text, re.I):
            reason = "official_source_update_unresolved"
        elif not name:
            reason = "missing_employer"
        elif notice and event in known_events:
            reason = "already_represented_la_row"
        elif _json_cells(row) in admitted_cells:
            reason, related = "duplicate_row_in_source", admitted_cells[_json_cells(row)]
        elif notice and site_event in admitted_events:
            # The same employer, site, notice date and count in another
            # year's table: a notice listed in two annual PDFs.
            reason, related = "duplicate_listing_in_other_annual_pdf", admitted_events[site_event]
        else:
            reason = None
        if company_cell.strip():
            previous = row["source_row"]
        if reason:
            item = {"origin": row["source_artifact"], "state": "LA", "reason": reason,
                    "source_row": row["source_row"],
                    "source_row_sha256": row["source_row_sha256"],
                    "source_url": row["source_url"],
                    "notice_year": notice[:4] if notice else str(row["year"]),
                    "raw_extra": json.dumps(row["cells"], ensure_ascii=False)}
            if related:
                item["related_source_row"] = related
            held.append(item)
            continue
        if notice:
            admitted_events.setdefault(site_event, row["source_row"])
        admitted_cells.setdefault(_json_cells(row), row["source_row"])
        start, end = _archive_span(layoff_cell)
        identity = f"LA:archive:{row['year']}:p{row['page']}:r{row['table_row']}"
        details = {
            "origin": row["source_artifact"], "source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"],
            "source_pdf_sha256": row["source_pdf_sha256"], "raw_cells": row["cells"],
            "report_year": row["year"],
            "employer_name_rule": "la_archive_lines_before_first_address_line_v1",
            "company_and_address_text": company_cell, "address_text": rest,
            "address_role": "unverified", "worker_allocation": "unverified",
            "date_interpretation": "interval" if end else "single_date" if start else "unparsed",
            "date_roles": {"Notice Date": "agency_table_notice_date",
                           "Layoff Date": "reported_action"},
            "industry_text": " ".join(industry.split()) or None,
        }
        rec = {
            "state": "LA", "employer_name": name, "location": None, "notice_date": notice,
            "effective_date": start, "effective_date_end": end,
            "employees_affected": workers, "layoff_type": "unknown",
            "is_temporary": None, "is_amendment": 0, "source_url": row["source_url"],
            "source_notice_id": None, "source_identity": identity,
            "source_details": json.dumps(details, sort_keys=True, ensure_ascii=False),
            "raw_extra": json.dumps(row["cells"], ensure_ascii=False),
            "dedupe_key": hashlib.sha1(f"LA|archive|{identity}".encode()).hexdigest(),
        }
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("Louisiana archive row accounting mismatch")
    blanked = archive_dates.apply(records, "LA", directory)
    return records, held, {
        "implausible_dates_blanked": blanked,
        "source_rows": len(rows), "admitted": len(records), "held": len(held),
        "hold_reasons": dict(sorted(Counter(item["reason"] for item in held).items())),
        "admitted_workers": sum(r["employees_affected"] or 0 for r in records),
        "admitted_missing_workers": sum(r["employees_affected"] is None for r in records),
        "admitted_missing_notice_date": sum(r["notice_date"] is None for r in records),
    }
