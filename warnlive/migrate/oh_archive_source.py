"""Ohio WARN page tables for 2023-2025, archived from the agency's site.

Input: ``agency/oh_archive`` in a source bundle (pinned in
``data/source_snapshots/oh/wayback-2026-09-30``): Wayback Machine captures of
ODJFS's "Public Notices of Layoffs and Closures" pages, whose table is
embedded as JSON (``js-placeholder-json-data``): the 2023 and 2024 annual
pages (captured 2025-06-06), the current-year page captured 2024-12-28 and
the current-year page captured 2025-10-31 (a partial 2025).

Output: ``project`` returns admitted notice records, held rows (for the
exception ledger) and a report. The unit is the agency Notice ID, as for the
2015-22 annual tables (``oh_annual_source``): repeated captures of one ID
are reconciled by ``classify_agency_ids`` and never become extra notices; an
ID already in the build, an irregular ID, an ID listing several phases or
changed content, and a row matching a current Ohio row's employer and
received day are held. "Date Received" is the agency's receipt date, kept as
``agency_received_date``; no notice-letter date is set. No network access.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections import Counter
from pathlib import Path

from warnlive.migrate.oh_annual_source import ID, _action, _date
from warnlive.normalize.engine import _record_hash
from warnlive.normalize.revisions import classify_agency_ids

PREFIX = "agency/oh_archive"
FORMAT = "oh-wayback-pages-v1"
FIELDS = ("Company", "Date Received", "URL", "City/County", "Potential Number Affected",
          "Layoff Date(s)", "Phone Number", "Union", "Notice ID")
OPTIONAL = ("Layoff/Closure",)
TABLE = re.compile(r'id="js-placeholder-json-data"[^>]*>(.*?)</div>', re.S)
MARKER = re.compile(r"\bupdat|\brevis", re.I)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _norm(value: object) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split())


def _table(content: bytes, name: str) -> list[dict]:
    """The page's JSON table as header-keyed rows; trailing blank rows dropped."""
    blocks = TABLE.findall(content.decode("utf-8"))
    if len(blocks) != 1:
        raise ValueError(f"Ohio page table not found once: {name}")
    data = json.loads(html.unescape(blocks[0]).strip())["data"]
    header = [_norm(cell) for cell in data[1]]
    named = [cell for cell in header if cell]
    if (not set(FIELDS) <= set(named) or set(named) - set(FIELDS) - set(OPTIONAL)
            or len(set(named)) != len(named)):
        raise ValueError(f"Ohio page header changed: {name}")
    rows = []
    for ordinal, cells in enumerate(data[2:], start=1):
        if all(not _norm(cell) for cell in cells):
            continue
        if len(cells) != len(header):
            raise ValueError(f"Ohio page row width changed: {name}:{ordinal}")
        extra = [_norm(cell) for cell, key in zip(cells, header) if not key and _norm(cell)]
        if extra:
            raise ValueError(f"Ohio page unlabeled cell used: {name}:{ordinal}")
        rows.append({"table_row": ordinal,
                     "fields": {key: str(cell) for key, cell in zip(header, cells) if key}})
    return rows


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = manifest.get("artifacts") or []
    if manifest.get("format") != FORMAT or len(specs) != 4:
        raise ValueError("unsupported Ohio archive manifest")
    rows = []
    for spec in specs:
        name = spec["file"]
        path = directory / name
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[a-z0-9-]+-\d{14}\.html", name):
            raise ValueError(f"invalid Ohio archive artifact: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != spec["bytes"] or digest != spec["sha256"]:
            raise ValueError(f"Ohio archive checksum mismatch: {name}")
        table = _table(content, name)
        if len(table) != spec["data_rows"]:
            raise ValueError(f"Ohio archive row count changed: {name}")
        for item in table:
            raw = item["fields"]
            rows.append({"file": name, "table_row": item["table_row"], "raw": raw,
                         "capture": spec["wayback_timestamp"], "page": spec["page"],
                         "source_artifact": f"{PREFIX}/{name}", "artifact_sha256": digest,
                         "source_row": f"{PREFIX}/{name}:sha256:{digest}:table_row:{item['table_row']}",
                         "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest(),
                         "source_url": spec["wayback_url"]})
    return rows, manifest


def _content(row: dict) -> str:
    """The notice facts of a row: contact and link cells are left out."""
    raw = row["raw"]
    return _json([_norm(raw["Company"]), _date(raw["Date Received"]) or _norm(raw["Date Received"]),
                  _norm(raw["City/County"]).casefold(), _norm(raw["Potential Number Affected"]),
                  _norm(raw["Layoff Date(s)"]), _norm(raw["Union"]), _norm(raw.get("Layoff/Closure"))])


def employer_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())[:12]


def project(directory: Path, existing_ids: set[str] | None = None,
            existing_events: set[tuple[str, str]] | None = None
            ) -> tuple[list[dict], list[dict], dict]:
    """Admit one notice per unambiguous Notice ID absent from the build.

    ``existing_ids`` are Ohio Notice IDs already read by the build (admitted
    or held); ``existing_events`` are (employer key, received day) of Ohio
    rows without an ID, such as the current page's.
    """
    rows, _ = read_artifacts(directory)
    existing_ids = {str(x).removeprefix("OH:") for x in (existing_ids or set())}
    existing_events = set(existing_events or ())
    for row in rows:
        raw = row["raw"]
        match = ID.fullmatch(_norm(raw["Notice ID"]).replace("‐", "-"))
        row["id"] = match.group(1) if match else None
        row["amended"] = any(MARKER.search(_norm(raw[key])) for key in
                             ("Notice ID", "Company", "Date Received", "URL"))
    decisions = classify_agency_ids(
        rows, row_key=lambda r: r["source_row"], agency_id=lambda r: r["id"],
        site=lambda r: _norm(r["raw"]["City/County"]).casefold(),
        action=lambda r: _action(_norm(r["raw"]["Layoff Date(s)"])),
        revision=lambda r: r["amended"], content=_content,
        preferred=lambda r: not r["amended"],
    )
    records, held = [], []
    for row in rows:
        raw, ident = row["raw"], row["id"]
        decision = decisions[row["source_row"]]
        received = _date(raw["Date Received"])
        event = (employer_key(raw["Company"]), received)
        reason = ("invalid_or_multiple_notice_id" if not ident else
                  "already_represented_oh_id" if ident in existing_ids else
                  "conflicting_original_notice_id" if decision.kind == "unresolved" else
                  "superseding_amendment_observation" if decision.kind == "revision" else
                  "duplicate_agency_capture" if decision.kind == "duplicate_capture" else
                  "amendment_without_original_listing" if row["amended"] else
                  "possible_overlap_with_current_oh_row" if received and event in existing_events else
                  "missing_employer" if not _norm(raw["Company"]) else None)
        if reason:
            held.append({"origin": row["source_artifact"], "state": "OH", "reason": reason,
                         "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
                         "disposition": "unresolved" if decision.kind == "notice" else decision.kind,
                         "preliminary_disposition": decision.kind,
                         "disposition_evidence": decision.evidence,
                         "related_source_row": decision.related_row,
                         "source_notice_id": ident or (_norm(raw["Notice ID"]) or None),
                         "source_url": row["source_url"],
                         "notice_year": received[:4] if received else None,
                         "raw_extra": _json(raw)})
            continue
        workers_text = _norm(raw["Potential Number Affected"]).replace(",", "")
        workers = int(workers_text) if workers_text.isdigit() and int(workers_text) > 0 else None
        action = _action(_norm(raw["Layoff Date(s)"]))
        kind = _norm(raw.get("Layoff/Closure")).casefold()
        details = {"origin": row["source_artifact"], "source_row": row["source_row"],
                   "source_row_sha256": row["source_row_sha256"],
                   "source_artifact_sha256": row["artifact_sha256"],
                   "identity_basis": "agency_WARN_or_Notice_ID",
                   "disposition": decision.kind, "disposition_evidence": decision.evidence,
                   "listing_page": row["page"], "wayback_capture": row["capture"],
                   "agency_received_date": received,
                   "notice_document_url": _norm(raw["URL"]) or None,
                   "date_roles": {"Date Received": "agency_receipt",
                                  "Layoff Date(s)": "reported_action"},
                   "raw_fields": raw}
        if kind:
            details["layoff_type_evidence"] = {"rule": "oh_page_layoff_closure_column_v1",
                                               "source_text": _norm(raw["Layoff/Closure"])}
        rec = {"state": "OH", "employer_name": _norm(raw["Company"]),
               "location": _norm(raw["City/County"]) or None,
               "notice_date": None, "effective_date": action,
               "effective_date_precision": "day" if action else None,
               "effective_date_basis": "reported" if action else None,
               "employees_affected": workers,
               "layoff_type": ("closure" if kind == "closure" else
                               "mass_layoff" if kind in ("layoff", "layoffs") else "unknown"),
               "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": ident,
               "source_identity": f"OH:{ident}",
               "source_details": _json({k: v for k, v in details.items() if v is not None}),
               "raw_extra": _json(raw),
               "dedupe_key": hashlib.sha1(f"OH|source|{ident}".encode()).hexdigest()}
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows) or len({r["dedupe_key"] for r in records}) != len(records):
        raise ValueError("Ohio archive row accounting mismatch")
    return records, held, {
        "source_rows": len(rows), "distinct_notice_ids": len({r["id"] for r in rows if r["id"]}),
        "admitted": len(records), "held": len(held),
        "hold_reasons": dict(sorted(Counter(x["reason"] for x in held).items())),
        "admitted_workers": sum(r["employees_affected"] or 0 for r in records),
        "admitted_missing_workers": sum(r["employees_affected"] is None for r in records),
        "admitted_missing_action_date": sum(r["effective_date"] is None for r in records),
    }
