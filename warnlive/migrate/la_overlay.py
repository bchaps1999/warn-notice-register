"""Reviewed Louisiana source overlay for the frozen, source-only rebuild.

The PDF is a table of reported notices, not a filing archive.  Keep the two
unresolved clusters out of active counts, and never infer address roles or
split a reported worker total across sites.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from warnlive.normalize.engine import _record_hash

LA_MANIFEST_SHA256 = "bbdd7a445f406f4f8b8153a4514b8eadf048a8653505b585d62d737e138ef6b7"
BLN_SHA256 = "99482f131d8e96660a5e1aaeaf65dbd5382321d41bd7e6348a105ae4be6d373e"

# This reviewed correspondence includes alternate BLN transcriptions, not just
# the rows selected by the conservative older/empty-month importer.  The
# checksum below pins the exact resolved correspondence and must be changed
# only after reviewing a new reconciliation report.
CORRESPONDENCE_SHA256 = "bb33fec85cd30726641495d6c0eed5d4affcff89e197588d944009101846d386"

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

# Two BLN transcriptions have notice/effective dates reversed.  The source
# itself, rather than either BLN date, is authoritative for the overlay.
SWAPPED = {
    "5cc947c4d699714a97e87323944d54515a164116813b602031a264f8": "2026.pdf:p1:r4",
    "50faf61775210526c05fca8903c0ed5c77c0b4ee1895f370618310d8": "2026.pdf:p1:r3",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def correspondence(rows: list[dict], la_dir: Path, bln_csv: Path) -> dict[str, dict]:
    """Return pinned BLN row evidence mapped to official source-row IDs."""
    if _sha(la_dir / "manifest.json") != LA_MANIFEST_SHA256:
        raise ValueError("Louisiana reviewed source manifest drift")
    if _sha(bln_csv) != BLN_SHA256:
        raise ValueError("Louisiana reviewed BLN input drift")
    notices = {row["source_row"]: row for row in rows if row["kind"] == "notice"}
    if len(rows) != 39 or len(notices) != 38 or set(notices) != set(EMPLOYERS) | HELD | {"2025.pdf:p2:r5"}:
        raise ValueError("Louisiana reviewed source rows drift")
    signatures: dict[tuple[str, str], list[str]] = defaultdict(list)
    for key, row in notices.items():
        signatures[(row["notice_date"] or "", str(row["workers_total"]))].append(key)
    mapped = {}
    with bln_csv.open(newline="") as fh:
        for ordinal, row in enumerate(csv.DictReader(fh), start=1):
            if (row.get("postal_code") or "").upper() != "LA":
                continue
            ident = row.get("hash_id")
            targets = signatures.get((row.get("notice_date") or "", row.get("jobs") or ""), [])
            if ident in SWAPPED:
                targets = sorted(set(targets) | {SWAPPED[ident]})
            if not targets:
                continue
            if not ident or ident in mapped:
                raise ValueError("Louisiana BLN correspondence has duplicate/missing ID")
            raw = json.dumps(row, sort_keys=True, ensure_ascii=False)
            dispositions = {"held" if target in HELD else
                            "rescinded" if notices[target]["status"] == "rescinded" else
                            "accepted" for target in targets}
            if len(dispositions) != 1:
                raise ValueError("Louisiana BLN correspondence has mixed dispositions")
            mapped[ident] = {
                "source_row": ordinal,
                "source_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "official_source_rows": sorted(targets),
                "disposition": dispositions.pop(),
                "match_basis": "reviewed_swapped_dates" if ident in SWAPPED else "reviewed_date_workers",
                "is_superseded": row.get("is_superseded") == "True",
            }
    digest = hashlib.sha256(json.dumps(mapped, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if digest != CORRESPONDENCE_SHA256:
        raise ValueError(f"Louisiana BLN correspondence drift: {digest}")
    return mapped


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
