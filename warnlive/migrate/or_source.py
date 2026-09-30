"""Verified Oregon agency workbooks projected to one notice per WARN number.

Input: the pinned ``agency/or`` cached workbook and optional live capture.
Output: one record per admitted WARN number (itemized ``sites``/``phases``
for a number listing several), a later version for a number whose live
capture changed, and held rows with reasons. No network access.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.store.dedupe import _dates_disagree
from warnlive.normalize.engine import _classify_from_raw, _record_hash, _temporary_from_raw
from warnlive.normalize.revisions import Disposition, classify_agency_ids

HEADERS = ("WARN#", "Company Name", "Location", "Layoff Date", "Laid Off",
           "Layoff Type", "Received Date")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    """Verify both agency exports and retain all source rows."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("format") != "warn-or-agency-export-v1" or manifest.get("workbook") != "latest.xlsx":
        raise ValueError("unsupported Oregon source manifest")
    rows = []
    sources = [("latest.xlsx", manifest)]
    live_manifest_path = directory / "live-manifest.json"
    if live_manifest_path.exists():
        live = json.loads(live_manifest_path.read_text())
        if live.get("format") != "warn-or-agency-live-v1" or live.get("artifact") != "live.xlsx":
            raise ValueError("unsupported Oregon live source manifest")
        sources.append(("live.xlsx", live))
    for name, item in sources:
        content = (directory / name).read_bytes()
        if (len(content) != item.get("bytes") or
                hashlib.sha256(content).hexdigest() != item.get("sha256")):
            raise ValueError("Oregon workbook checksum mismatch")
        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        source = list(sheet.values)
        if (sheet.title != item.get("sheet_title") or len(source) < 4 or
                tuple(source[2]) != HEADERS or list(HEADERS) != item.get("headers") or
                len(source) - 3 != item.get("data_rows")):
            raise ValueError("Oregon workbook layout drift")
        for ordinal, cells in enumerate(source[3:], start=4):
            if len(cells) != len(HEADERS):
                raise ValueError(f"Oregon row width drift: {ordinal}")
            raw = {key: value.isoformat() if isinstance(value, datetime) else value
                   for key, value in zip(HEADERS, cells, strict=True)}
            rows.append({"source_row": ordinal, "source_artifact": f"agency/or/{name}",
                         "snapshot": "live" if name == "live.xlsx" else "cached",
                         "captured_on": item.get("retrieved_on_utc") or item.get("cache_filesystem_mtime"),
                         "source_sha256": item["sha256"], "raw": raw,
                         "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest()})
    return rows, manifest


# classify_agency_ids evidence for an agency ID listing several sites or
# phases: one filing whose itemized rows the notice keeps.
ITEMIZED_EVIDENCE = {"multiple_sites_under_agency_id": "sites",
                     "multiple_phases_under_agency_id": "phases"}


def type_fields(text: object) -> tuple[str, int | None, dict | None]:
    """layoff_type and is_temporary from "Layoff Type", exactly as live reads it.

    The live scrape (warn-transformer's Oregon checks, then the engine's raw
    type-column fallbacks) reads the same column; calling the same fallbacks
    keeps the replay and the scrape in agreement.
    """
    value = str(text or "").strip()
    if not value:
        return "unknown", None, None
    lowered = value.lower()
    column = {"Layoff Type": value}
    layoff_type = ("closure" if "closure" in lowered else
                   _classify_from_raw(column) or "unknown")
    is_temporary = 1 if "temporary" in lowered else _temporary_from_raw(column)
    return layoff_type, is_temporary, {"rule": "or_layoff_type_live_mapping_v1",
                                       "source_field": "Layoff Type", "source_text": value}


def row_complete(ident: str, employer: str, received: str | None,
                 effective: str | None, workers: object) -> bool:
    return bool(ident.isdigit() and employer and received and effective
                and isinstance(workers, (int, float)) and not isinstance(workers, bool)
                and workers > 0 and int(workers) == workers)


def single_record(ident: str, item: dict, details: dict, source_url: str) -> dict:
    """One agency row as the notice for its WARN number.

    A row admitted without an action date or worker count (the historical
    list's partial-row rule) leaves those fields null.
    """
    raw = item["raw"]
    layoff_type, is_temporary, type_evidence = type_fields(raw["Layoff Type"])
    if type_evidence:
        details = {**details, "type_evidence": type_evidence}
    rec = {
        "state": "OR", "employer_name": str(raw["Company Name"]).strip(),
        "location": str(raw["Location"] or "").strip() or None,
        "notice_date": None, "effective_date": item["effective"],
        "effective_date_precision": "day" if item["effective"] else None,
        "effective_date_basis": "reported" if item["effective"] else None,
        "employees_affected": int(item["workers"]) if item["workers"] is not None else None,
        "layoff_type": layoff_type,
        "is_temporary": is_temporary, "is_amendment": 0,
        "source_url": source_url, "source_notice_id": ident,
        "source_identity": f"OR:agency:{ident}",
        "source_details": _json(details), "raw_extra": _json(raw),
        "dedupe_key": hashlib.sha1(f"OR|agency|{ident}".encode()).hexdigest(),
    }
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def filing_record(ident: str, items: list[dict], kind: str, details: dict,
                  source_url: str) -> dict:
    """One WARN number listing several sites or phases, as one filing notice.

    ``items`` are the number's rows in source order, each complete. Workers
    are the sum of the agency's own itemized counts when every row is a
    distinct site/date. Two rows with the same site (Company Name, which
    often names a facility, and Location) and layoff date may be a
    count and its restatement, so a filing with such a pair is not summed: ``employees_affected`` is
    left unknown, ``worker_allocation`` is ``"unresolved"`` and the pairs are
    listed. The action date is the
    earliest listed and the end the latest (``list_or_phases``). The employer
    is the first listed row's Company Name, which for some filings names a
    facility; every row's name and location stays in ``sites``/``phases``.
    """
    entries = []
    for item in items:
        raw = item["raw"]
        entries.append({"source_row": item["source_row"],
                        "source_row_sha256": item["source_row_sha256"],
                        "company_name": str(raw["Company Name"]).strip(),
                        "location": str(raw["Location"] or "").strip() or None,
                        "effective_date": item["effective"],
                        "workers": int(item["workers"]),
                        "layoff_type_text": str(raw["Layoff Type"] or "").strip() or None})
    starts = sorted(entry["effective_date"] for entry in entries)
    locations = {entry["location"].casefold(): entry["location"]
                 for entry in entries if entry["location"]}
    types = {type_fields(item["raw"]["Layoff Type"])[:2] for item in items}
    layoff_type, is_temporary = types.pop() if len(types) == 1 else ("unknown", None)
    texts = {entry["layoff_type_text"] for entry in entries}
    by_site_date: dict[tuple, list[dict]] = {}
    for entry in entries:
        # A row's Company Name often names its facility, so the site is the
        # name and the location together.
        site = (entry["company_name"].casefold(), (entry["location"] or "").casefold())
        by_site_date.setdefault((site, entry["effective_date"]), []).append(entry)
    repeated = [group for group in by_site_date.values() if len(group) > 1]
    details = {**details, kind: entries,
               "worker_allocation": "unresolved" if repeated else "itemized",
               "total_workers_basis": ("not_summed_same_site_and_date_rows" if repeated
                                       else f"sum_of_listed_{kind}"),
               "employer_name_basis": "first_listed_row",
               "effective_date_interpretation": "list_or_phases"}
    if repeated:
        details["same_site_date_rows"] = [
            {"company_name": group[0]["company_name"], "location": group[0]["location"],
             "effective_date": group[0]["effective_date"],
             "workers": [entry["workers"] for entry in group],
             "source_rows": [entry["source_row"] for entry in group]} for group in repeated]
    if len(texts) == 1 and next(iter(texts)):
        details["type_evidence"] = type_fields(next(iter(texts)))[2]
    rec = {
        "state": "OR", "employer_name": entries[0]["company_name"],
        "location": next(iter(locations.values())) if len(locations) == 1 else None,
        "notice_date": None, "effective_date": starts[0],
        "effective_date_precision": "day", "effective_date_basis": "reported",
        "employees_affected": None if repeated else sum(entry["workers"] for entry in entries),
        "layoff_type": layoff_type, "is_temporary": is_temporary, "is_amendment": 0,
        "source_url": source_url, "source_notice_id": ident,
        "source_identity": f"OR:agency:{ident}",
        "source_details": _json(details),
        "raw_extra": _json([item["raw"] for item in items]),
        "dedupe_key": hashlib.sha1(f"OR|agency|{ident}".encode()).hexdigest(),
    }
    if starts[-1] != starts[0]:
        rec.update(effective_date_end=starts[-1], effective_date_end_precision="day",
                   effective_date_end_basis="reported")
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def project(directory: Path) -> tuple[list[dict], list[dict], dict]:
    """Admit each complete agency WARN number as one notice.

    A WARN number listed once is its notice. One listing several sites or
    phases is one filing notice with ``sites``/``phases`` and summed workers.
    Conflicting unmarked rows under one number stay held. A number whose
    later (live) capture changed becomes a second version of the cached
    notice, ordered by capture date; identical re-captures stay held as
    duplicates. Records then outnumber notices by
    ``report["capture_versions"]``.
    """
    rows, manifest = read_artifacts(directory)
    cached = [row for row in rows if row["snapshot"] == "cached"]
    live = [row for row in rows if row["snapshot"] == "live"]
    cached_ids = {str(row["raw"]["WARN#"] or "") for row in cached}
    candidates = cached + [row for row in live
                           if str(row["raw"]["WARN#"] or "") not in cached_ids]
    def pointer(row: dict) -> str:
        return f"{row['source_artifact']}:row:{row['source_row']}:sha256:{row['source_row_sha256']}"

    def classify(group_rows: list[dict]) -> dict:
        return classify_agency_ids(
            group_rows, row_key=pointer,
            agency_id=lambda r: str(r["raw"]["WARN#"] or "").strip() or None,
            site=lambda r: str(r["raw"]["Location"] or "").casefold().strip(),
            action=lambda r: str(r["raw"]["Layoff Date"] or "")[:10] or None,
            revision=lambda r: False, content=lambda r: _json(r["raw"]),
        )

    def item(row: dict) -> dict:
        raw = row["raw"]
        received, effective = raw["Received Date"], raw["Layoff Date"]
        received = received[:10] if isinstance(received, str) and len(received) >= 10 else None
        effective = effective[:10] if isinstance(effective, str) and len(effective) >= 10 else None
        return {"source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
                "raw": raw, "received": received, "effective": effective,
                "workers": raw["Laid Off"],
                "complete": row_complete(str(raw["WARN#"] or ""),
                                         str(raw["Company Name"] or "").strip(),
                                         received, effective, raw["Laid Off"])}

    def base_details(row: dict, disposition: str, evidence: str, identity_basis: str,
                     received: str) -> dict:
        return {"origin": row["source_artifact"], "source_workbook_sha256": row["source_sha256"],
                "identity_basis": identity_basis,
                "disposition": disposition, "disposition_evidence": evidence,
                "agency_received_date": received,
                "date_roles": {"Received Date": "agency_receipt", "Layoff Date": "reported_action"}}

    def project_group(ident: str, group_rows: list[dict], decisions: dict) -> dict | None:
        """The notice for one WARN number's rows, or None when it stays held."""
        items = [item(row) for row in group_rows]
        first = group_rows[0]
        decision = decisions[pointer(first)]
        if len(group_rows) == 1:
            if decision.kind != "notice" or not items[0]["complete"]:
                return None
            details = {**base_details(first, decision.kind, decision.evidence,
                                      "singleton_agency_warn_number", items[0]["received"]),
                       "source_row": first["source_row"],
                       "source_row_sha256": first["source_row_sha256"],
                       "raw_cells": first["raw"]}
            return single_record(ident, items[0], details, manifest["source_url"])
        evidence = {decisions[pointer(row)].evidence for row in group_rows}
        if (len(evidence) != 1 or next(iter(evidence)) not in ITEMIZED_EVIDENCE
                or not all(entry["complete"] for entry in items)
                or len({entry["received"] for entry in items}) != 1):
            return None
        evidence_name = next(iter(evidence))
        details = base_details(first, "notice", evidence_name, "agency_warn_number_filing",
                               items[0]["received"])
        return filing_record(ident, items, ITEMIZED_EVIDENCE[evidence_name], details,
                             manifest["source_url"])

    decisions = classify(candidates)
    groups: dict[str, list[dict]] = {}
    for row in candidates:
        groups.setdefault(str(row["raw"]["WARN#"] or ""), []).append(row)
    live_groups: dict[str, list[dict]] = {}
    for row in live:
        live_groups.setdefault(str(row["raw"]["WARN#"] or ""), []).append(row)

    records, exceptions = [], []
    admitted: dict[str, dict] = {}
    covered: set[str] = set()
    for ident, group_rows in groups.items():
        # Identical copies are held as duplicate captures of the kept row.
        kept = [row for row in group_rows
                if decisions[pointer(row)].kind != "duplicate_capture"]
        rec = project_group(ident, kept, decisions) if ident.isdigit() and kept else None
        if rec is not None:
            admitted[ident] = rec
            covered.update(pointer(row) for row in kept)
    # A changed later capture of an admitted number is its next version.
    versions: dict[str, tuple[dict, list[dict]]] = {}
    for ident, later_rows in live_groups.items():
        if ident not in cached_ids or ident not in admitted:
            continue
        older = sorted(_json(row["raw"]) for row in groups[ident])
        if sorted(_json(row["raw"]) for row in later_rows) == older:
            continue
        later_decisions = classify(later_rows)
        rec = project_group(ident, [row for row in later_rows if later_decisions[
            pointer(row)].kind != "duplicate_capture"], later_decisions)
        if rec is None or _dates_disagree(admitted[ident]["effective_date"], rec["effective_date"]):
            continue
        details = json.loads(rec["source_details"])
        details["capture_version"] = {
            "basis": "agency_capture_order", "snapshot": "live",
            "retrieved_on": later_rows[0].get("captured_on"),
            "previous_capture": "agency/or/latest.xlsx"}
        rec["source_details"] = _json(details)
        rec["raw_record_hash"] = _record_hash(rec)
        changed = [row for row in later_rows if _json(row["raw"]) not in set(older)]
        versions[ident] = (rec, changed)
        covered.update(pointer(row) for row in changed)
    for ident, rec in admitted.items():
        records.append(rec)
    records.extend(rec for rec, _ in versions.values())

    for row in rows:
        if pointer(row) in covered:
            continue
        raw = row["raw"]
        ident = str(raw["WARN#"] or "")
        decision = decisions.get(pointer(row), Disposition("unresolved", "later_capture_of_existing_warn_number"))
        if row["snapshot"] == "live" and ident in cached_ids:
            exceptions.append({
                "origin": row["source_artifact"], "state": "OR",
                "reason": "later_capture_of_existing_warn_number",
                "disposition": "duplicate_capture" if any(
                    old["raw"] == raw for old in cached if str(old["raw"]["WARN#"] or "") == ident
                ) else "unresolved",
                "disposition_evidence": "same_agency_id_later_capture",
                "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
                "source_notice_id": ident or None, "source_url": manifest["source_url"],
                "notice_year": None, "raw_extra": _json(raw),
            })
            continue
        reason = ("duplicate_agency_capture" if decision.kind == "duplicate_capture" else
                  "incomplete_agency_row" if decision.kind == "notice" else
                  "itemized_filing_incomplete" if decision.evidence in ITEMIZED_EVIDENCE else
                  "multi_site_or_phase_identity_unresolved")
        exceptions.append({
            "origin": row["source_artifact"], "state": "OR", "reason": reason,
            "disposition": "unresolved" if decision.kind == "notice" else decision.kind,
            "preliminary_disposition": decision.kind,
            "disposition_evidence": decision.evidence,
            "related_source_row": decision.related_row,
            "source_row": row["source_row"], "source_row_sha256": row["source_row_sha256"],
            "source_notice_id": ident or None, "source_url": manifest["source_url"],
            "notice_year": None, "raw_extra": _json(raw),
        })
    if len(covered) + len(exceptions) != len(rows):
        raise ValueError("Oregon source row accounting mismatch")
    return records, exceptions, {"source_rows": len(rows),
                                 "admitted": len(records) - len(versions),
                                 "admitted_rows": len(covered),
                                 "itemized_filings": sum(
                                     json.loads(rec["source_details"]).get("worker_allocation")
                                     == "itemized" for rec in admitted.values()),
                                 "capture_versions": len(versions),
                                 "held": len(exceptions),
                                 "hold_reasons": dict(Counter(x["reason"] for x in exceptions)),
                                 "duplicate_captures": sum(x.get("disposition") == "duplicate_capture" for x in exceptions)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Add Oregon agency evidence to an agency-only bundle without raw/OR."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base_manifest = verify(base_bundle)
    if base_manifest.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Oregon overlay requires an agency-only base")
    project(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if "raw/or.csv" in files or any(name.startswith("agency/or/") for name in files):
        raise ValueError("base bundle already contains Oregon source")
    names = ["manifest.json", "latest.xlsx"]
    if (artifacts / "live-manifest.json").exists():
        names += ["live-manifest.json", "live.xlsx"]
    for name in names:
        files[f"agency/or/{name}"] = (artifacts / name).read_bytes()
    manifest = {**base_manifest, "files": [
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base, args.artifacts, args.out)
    print(json.dumps({"files": len(result["files"]), "out": str(args.out)}, sort_keys=True))


if __name__ == "__main__":
    main()
