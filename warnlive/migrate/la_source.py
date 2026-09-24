"""Read frozen Louisiana WARN tables without guessing canonical notice identity.

This is an evidence projection, not a database importer. The agency's 2025
layout combines employer and address text; its 2026 layout separates them but
does not establish whether every address is a worksite. Preserve both verbatim.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pdfplumber

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
