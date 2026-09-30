"""Agency-sent Oregon historical WARN list, preserved by Big Local News.

Input: the pinned ``agency/or_historical`` workbook and the WARN numbers in
the newer captures. Output: one record per admitted WARN number (itemized
``sites``/``phases`` for a number listing several) and held rows with
reasons. No network access.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from warnlive.migrate.or_source import (HEADERS, ITEMIZED_EVIDENCE, filing_record,
                                        row_complete, single_record)
from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.revisions import classify_agency_ids

ARTIFACT = "or_warnlist_july_2021.xlsx"
PREFIX = "agency/or_historical"
# These pinned rows put only a street/city in Company Name and have no location.
# The employer cannot be recovered from this workbook alone.
UNRESOLVED_EMPLOYER_IDS = {"0826", "0810", "0809", "0742", "0727", "0708"}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))


def _day(value: object) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"Oregon historical date is not a day: {value!r}") from exc
    if not isinstance(value, datetime) or value.time() != datetime.min.time():
        raise ValueError(f"Oregon historical date is not a day: {value!r}")
    # Excel's zero-date sentinel is an empty action date, not a 19th-century layoff.
    if value.date().isoformat() == "1899-12-29":
        return None
    return value.date().isoformat()


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    """Check exact workbook bytes/layout and preserve every physical data row."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    path = directory / ARTIFACT
    if (manifest.get("format") != "agency-sent-historical-workbook-v1"
            or manifest.get("file") != ARTIFACT
            or manifest.get("sheet") != "WARNList"
            or manifest.get("headers") != list(HEADERS)
            or manifest.get("data_rows") != 1082
            or path.is_symlink()):
        raise ValueError("unsupported Oregon historical manifest")
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if len(content) != manifest.get("bytes") or digest != manifest.get("sha256"):
        raise ValueError("Oregon historical workbook checksum mismatch")
    book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        if book.sheetnames != ["WARNList"]:
            raise ValueError("Oregon historical workbook sheet changed")
        values = list(book.active.values)
    finally:
        book.close()
    if (len(values) != 1085 or tuple(values[2]) != HEADERS
            or values[1][1] != manifest.get("banner")):
        raise ValueError("Oregon historical workbook layout changed")
    rows = []
    for ordinal, cells in enumerate(values[3:], start=4):
        if len(cells) != len(HEADERS):
            raise ValueError(f"Oregon historical row width changed: {ordinal}")
        raw = {key: value.isoformat() if isinstance(value, datetime) else value
               for key, value in zip(HEADERS, cells, strict=True)}
        rows.append({"source_row": ordinal, "raw": raw,
                     "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest()})
    return rows, manifest


def project(directory: Path, existing_ids: set[str] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Admit complete WARN numbers absent from both newer captures.

    A number listed once is its notice; one listing several sites or phases
    is one filing notice with itemized ``sites``/``phases`` (see
    ``or_source.filing_record``). Conflicting rows stay held.
    """
    rows, manifest = read_artifacts(directory)
    existing = existing_ids or set()
    def pointer(row: dict) -> str:
        return f"{PREFIX}/{ARTIFACT}:sha256:{manifest['sha256']}:row:{row['source_row']}"

    decisions = classify_agency_ids(
        rows, row_key=pointer,
        agency_id=lambda r: str(r["raw"]["WARN#"] or "").strip() or None,
        site=lambda r: str(r["raw"]["Location"] or "").casefold().strip(),
        action=lambda r: str(r["raw"]["Layoff Date"] or "")[:10] or None,
        revision=lambda r: False, content=lambda r: _json(r["raw"]),
    )
    items: dict[str, dict] = {}
    groups: dict[str, list[dict]] = {}
    for row in rows:
        raw = row["raw"]
        ident = str(raw["WARN#"] or "").strip()
        try:
            received = _day(raw["Received Date"])
            effective = _day(raw["Layoff Date"])
        except ValueError:
            received = effective = None
        items[pointer(row)] = {
            "source_row": pointer(row), "source_row_sha256": row["source_row_sha256"],
            "raw": raw, "received": received, "effective": effective,
            "workers": raw["Laid Off"],
            "complete": row_complete(ident, str(raw["Company Name"] or "").strip(),
                                     received, effective, raw["Laid Off"])}
        groups.setdefault(ident, []).append(row)

    def details_for(evidence: str, identity_basis: str, received: str) -> dict:
        return {"origin": f"{PREFIX}/{ARTIFACT}",
                "source_workbook_sha256": manifest["sha256"],
                "provenance_url": manifest["provenance_url"],
                "identity_basis": identity_basis,
                "disposition": "notice", "disposition_evidence": evidence,
                "agency_received_date": received,
                "date_roles": {"Received Date": "agency_receipt",
                               "Layoff Date": "reported_action"}}

    filings: dict[str, dict] = {}
    for ident, group_rows in groups.items():
        evidence = {decisions[pointer(row)].evidence for row in group_rows}
        members = [items[pointer(row)] for row in group_rows]
        if (len(group_rows) > 1 and ident.isdigit() and ident not in existing
                and ident not in UNRESOLVED_EMPLOYER_IDS
                and len(evidence) == 1 and next(iter(evidence)) in ITEMIZED_EVIDENCE
                and all(member["complete"] for member in members)
                and len({member["received"] for member in members}) == 1):
            name = next(iter(evidence))
            filings[ident] = filing_record(
                ident, members, ITEMIZED_EVIDENCE[name],
                details_for(name, "agency_warn_number_filing", members[0]["received"]),
                manifest["source_url"])

    records, held = [], []
    admitted_rows = 0
    for row in rows:
        raw = row["raw"]
        ident = str(raw["WARN#"] or "").strip()
        employer = str(raw["Company Name"] or "").strip()
        entry = items[pointer(row)]
        received = entry["received"]
        decision = decisions[pointer(row)]
        if ident in filings:
            admitted_rows += 1
            if row is groups[ident][0]:
                records.append(filings[ident])
            continue
        reason = ("missing_warn_number" if not ident.isdigit() else
                  "newer_agency_capture_overlap" if ident in existing else
                  "duplicate_agency_capture" if decision.kind == "duplicate_capture" else
                  "itemized_filing_incomplete" if decision.kind == "unresolved"
                  and decision.evidence in ITEMIZED_EVIDENCE
                  and ident not in UNRESOLVED_EMPLOYER_IDS else
                  "multi_site_or_phase_identity_unresolved" if decision.kind == "unresolved" else
                  "unresolved_employer_in_source" if ident in UNRESOLVED_EMPLOYER_IDS else
                  "incomplete_historical_row" if not entry["complete"] or not employer else None)
        source_pointer = pointer(row)
        if reason:
            held.append({"origin": f"{PREFIX}/{ARTIFACT}", "state": "OR",
                         "reason": reason, "source_row": source_pointer,
                         "disposition": "unresolved" if decision.kind == "notice" else decision.kind,
                         "preliminary_disposition": decision.kind,
                         "disposition_evidence": decision.evidence,
                         "related_source_row": decision.related_row,
                         "source_row_sha256": row["source_row_sha256"],
                         "source_notice_id": ident or None,
                         "source_url": manifest["source_url"],
                         "notice_year": received[:4] if received else None,
                         "raw_extra": _json(raw)})
            continue
        details = {**details_for(decision.evidence, "singleton_agency_warn_number", received),
                   "source_row": source_pointer,
                   "source_row_sha256": row["source_row_sha256"],
                   "disposition": decision.kind, "raw_cells": raw}
        records.append(single_record(ident, entry, details, manifest["source_url"]))
        admitted_rows += 1
    if admitted_rows + len(held) != len(rows):
        raise ValueError("Oregon historical source accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "admitted_rows": admitted_rows,
                           "itemized_filings": len(filings),
                           "held": len(held),
                           "hold_reasons": dict(Counter(item["reason"] for item in held)),
                           "duplicate_captures": sum(x.get("disposition") == "duplicate_capture" for x in held)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Add the pinned original workbook and provenance without overwriting sources."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Oregon historical overlay requires an agency-only base")
    read_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith(PREFIX + "/") for name in files):
        raise ValueError("base bundle already contains Oregon historical source")
    for name in ("manifest.json", ARTIFACT):
        files[f"{PREFIX}/{name}"] = (artifacts / name).read_bytes()
    manifest = {**base, "files": [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items())]}
    out_bundle.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_bundle.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            header = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
            for name, content in sorted(files.items()):
                archive.addfile(_entry(name, content), io.BytesIO(content))
        verify(out_bundle)
    except BaseException:
        out_bundle.unlink(missing_ok=True)
        raise
    return manifest
