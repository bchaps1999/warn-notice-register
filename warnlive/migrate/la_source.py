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
No network access.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

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
