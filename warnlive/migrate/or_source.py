"""Verified Oregon agency workbook and conservative one-row WARN projection."""

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
from warnlive.normalize.engine import _record_hash
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
                         "source_sha256": item["sha256"], "raw": raw,
                         "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest()})
    return rows, manifest


def project(directory: Path) -> tuple[list[dict], list[dict], dict]:
    """Admit only singleton agency WARN IDs with complete event fields.

    A repeated WARN number may contain several sites or phases. Those rows
    remain held rather than being counted as independent notices or summed.
    """
    rows, manifest = read_artifacts(directory)
    cached = [row for row in rows if row["snapshot"] == "cached"]
    live = [row for row in rows if row["snapshot"] == "live"]
    cached_ids = {str(row["raw"]["WARN#"] or "") for row in cached}
    candidates = cached + [row for row in live
                           if str(row["raw"]["WARN#"] or "") not in cached_ids]
    def pointer(row: dict) -> str:
        return f"{row['source_artifact']}:row:{row['source_row']}:sha256:{row['source_row_sha256']}"

    decisions = classify_agency_ids(
        candidates, row_key=pointer,
        agency_id=lambda r: str(r["raw"]["WARN#"] or "").strip() or None,
        site=lambda r: str(r["raw"]["Location"] or "").casefold().strip(),
        action=lambda r: str(r["raw"]["Layoff Date"] or "")[:10] or None,
        revision=lambda r: False, content=lambda r: _json(r["raw"]),
    )
    changed_singletons = set()
    for ident in cached_ids:
        older = [row["raw"] for row in cached if str(row["raw"]["WARN#"] or "") == ident]
        newer = [row["raw"] for row in live if str(row["raw"]["WARN#"] or "") == ident]
        if newer and len(set(map(_json, older))) == 1 and set(map(_json, older)) != set(map(_json, newer)):
            changed_singletons.add(ident)
    records, exceptions = [], []
    for row in rows:
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
        received = raw["Received Date"]
        effective = raw["Layoff Date"]
        workers = raw["Laid Off"]
        complete = (ident.isdigit() and bool(str(raw["Company Name"] or "").strip())
                    and isinstance(received, str) and len(received) >= 10
                    and isinstance(effective, str) and len(effective) >= 10
                    and isinstance(workers, (int, float)) and workers > 0
                    and int(workers) == workers)
        if decision.kind != "notice" or not complete or ident in changed_singletons:
            reason = ("changed_between_agency_captures" if ident in changed_singletons else
                      "duplicate_agency_capture" if decision.kind == "duplicate_capture" else
                      "multi_site_or_phase_identity_unresolved" if decision.kind == "unresolved" else
                      "incomplete_agency_row")
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
            continue
        details = {
            "origin": row["source_artifact"], "source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"],
            "source_workbook_sha256": row["source_sha256"],
            "identity_basis": "singleton_agency_warn_number",
            "disposition": decision.kind, "disposition_evidence": decision.evidence,
            "agency_received_date": received[:10],
            "date_roles": {"Received Date": "agency_receipt", "Layoff Date": "reported_action"},
            "raw_cells": raw,
        }
        rec = {
            "state": "OR", "employer_name": str(raw["Company Name"]).strip(),
            "location": str(raw["Location"] or "").strip() or None,
            "notice_date": None, "effective_date": effective[:10],
            "effective_date_precision": "day", "effective_date_basis": "reported",
            "employees_affected": int(workers), "layoff_type": "unknown",
            "is_temporary": None, "is_amendment": 0,
            "source_url": manifest["source_url"], "source_notice_id": ident,
            "source_identity": f"OR:agency:{ident}",
            "source_details": _json(details), "raw_extra": _json(raw),
            "dedupe_key": hashlib.sha1(f"OR|agency|{ident}".encode()).hexdigest(),
        }
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(exceptions) != len(rows):
        raise ValueError("Oregon source row accounting mismatch")
    return records, exceptions, {"source_rows": len(rows), "admitted": len(records),
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
