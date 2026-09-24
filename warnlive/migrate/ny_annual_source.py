"""Verified annual NY DOL Tableau observations, with conservative admission."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import tarfile
from collections import Counter
from pathlib import Path

from warnlive.migrate.ny_overlay import _employer_group
from warnlive.migrate.ny_source import HEADERS, _date
from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash
from warnlive.normalize.revisions import classify_idless_events

YEARS = tuple(range(2006, 2027))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def read_artifacts(directory: Path) -> list[dict]:
    """Verify every byte, header, row, duplicated worker cell, and year."""
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    artifacts = manifest.get("artifacts")
    if (manifest.get("format") != "ny-tableau-annual-direct-v1"
            or not isinstance(artifacts, list)
            or len(artifacts) != len(YEARS)
            or {item.get("year") for item in artifacts} != set(YEARS)):
        raise ValueError("unsupported NY annual source manifest")
    result = []
    for item in sorted(artifacts, key=lambda x: x["year"]):
        year = item["year"]
        name = f"ny_warn_{year}.csv"
        path = directory / name
        if item.get("file") != name or item.get("headers") != list(HEADERS) or path.is_symlink():
            raise ValueError(f"NY annual manifest schema mismatch: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != item.get("bytes") or digest != item.get("sha256"):
            raise ValueError(f"NY annual source checksum mismatch: {name}")
        count = 0
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            if tuple(next(reader, ())) != HEADERS:
                raise ValueError(f"NY annual CSV header changed: {name}")
            for ordinal, cells in enumerate(reader, start=2):
                if len(cells) != len(HEADERS) or cells[10] != cells[11]:
                    raise ValueError(f"NY annual CSV worker/layout mismatch: {name}:{ordinal}")
                notice = _date(cells[2], "notice date", ordinal)
                effective = _date(cells[1], "action date", ordinal)
                posted = _date(cells[3], "posted date", ordinal)
                if not notice or notice[:4] != str(year) or not cells[0].strip():
                    raise ValueError(f"NY annual source year/employer mismatch: {name}:{ordinal}")
                raw = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
                row_sha = hashlib.sha256(raw.encode()).hexdigest()
                result.append({"source_row_id": f"agency/ny_annual/{name}:sha256:{digest}:row:{ordinal}:sha256:{row_sha}",
                               "artifact": f"agency/ny_annual/{name}",
                               "artifact_sha256": digest, "row_sha256": row_sha,
                               "row_number": ordinal, "source_url": item["url"],
                               "raw_cells": cells, "company": cells[0],
                               "effective_date": effective, "notice_date": notice,
                               "posted_date": posted, "address": cells[4],
                               "county": cells[5], "event_type": cells[6],
                               "permanence": cells[7], "reason": cells[8],
                               "index": cells[9], "workers": cells[10]})
                count += 1
        if count != item.get("rows"):
            raise ValueError(f"NY annual CSV row count changed: {name}")
    return result


def project(directory: Path, existing_employers: set[str] | None = None,
            existing_site_actions: set[tuple[str, str, int]] | None = None,
            existing_events: set[tuple[str, str, str]] | None = None,
            existing_notices: list[dict] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Admit distinct employer/site/events and retain ambiguous observations.

    Existing canonical employers are screened after raw and archive ingestion.
    Dashboard Index is not a filing ID; source observation identity is explicit.
    """
    rows = read_artifacts(directory)
    # Names alone cannot identify a filing. Keep this parameter for callers;
    # event-level overlap is screened through existing_site_actions below.
    _ = existing_employers
    existing_sites = existing_site_actions or set()
    existing_event_keys = existing_events or set()
    by_notice: dict[tuple[str, str], list[dict]] = {}
    by_action: dict[tuple[str, str], list[dict]] = {}
    for prior in existing_notices or []:
        group = _employer_group(prior.get("employer_name") or "")
        if group and prior.get("notice_date"):
            by_notice.setdefault((group, prior["notice_date"]), []).append(prior)
        if group and prior.get("effective_date"):
            by_action.setdefault((group, prior["effective_date"]), []).append(prior)
    decisions = classify_idless_events(
        rows, row_key=lambda r: r["source_row_id"],
        employer=lambda r: _employer_group(r["company"]),
        site=lambda r: r["address"].casefold().strip(),
        action=lambda r: r["effective_date"], notice=lambda r: r["notice_date"],
        content=lambda r: _json(r["raw_cells"]),
    )
    by_filing_signature: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        # One employer notice may enumerate sites with different phase dates.
        # The dashboard has no filing ID to prove those are separate filings.
        signature = (_employer_group(row["company"]), row["notice_date"])
        by_filing_signature.setdefault(signature, []).append(row)
    records, held = [], []
    for row in rows:
        decision = decisions[row["source_row_id"]]
        worker_text = row["workers"].replace(",", "").strip()
        workers = int(worker_text) if worker_text.isdigit() and int(worker_text) > 0 else None
        group = _employer_group(row["company"])
        matches = {}
        for prior in by_notice.get((group, row["notice_date"]), []):
            matches[prior.get("id") or prior.get("source_notice_id")] = prior
        if row["effective_date"]:
            for prior in by_action.get((group, row["effective_date"]), []):
                matches[prior.get("id") or prior.get("source_notice_id")] = prior
        candidates = list(matches.values())
        exact_county = [prior for prior in candidates if
                        prior.get("notice_date") == row["notice_date"] and
                        prior.get("employees_affected") == workers and
                        str(prior.get("location") or "").casefold().strip() ==
                        row["county"].casefold().strip()]
        correspondence = ("same_employer_notice_date_workers_county" if len(exact_county) == 1 else
                          "same_employer_notice_or_action_date" if candidates else None)
        filing_group = by_filing_signature[(group, row["notice_date"])]
        sites_in_group = {member["address"].casefold().strip() for member in filing_group}
        multi_site = len(sites_in_group) > 1
        reason = ("multi_site_filing_identity_unresolved" if multi_site else
                  "existing_notice_event_correspondence_unresolved" if candidates else
                  "possible_revision_same_event" if decision.evidence == "possible_revision_same_event" else
                  "incomplete_dashboard_row" if decision.kind == "unresolved" else
                  "duplicate_dashboard_capture" if decision.kind == "duplicate_capture" else
                  "existing_employer_site_action_identity_unresolved" if row["effective_date"] and
                  (_employer_group(row["company"]), row["address"].casefold().strip(),
                   row["effective_date"]) in existing_event_keys else
                  "existing_site_action_worker_identity_unresolved" if
                  workers is not None and
                  (row["address"].casefold().strip(), row["effective_date"], workers) in existing_sites else None)
        if reason:
            held.append({"origin": row["artifact"], "state": "NY", "reason": reason,
                         "disposition": ("unresolved" if multi_site or candidates or decision.kind == "notice"
                                         else decision.kind),
                         "preliminary_disposition": decision.kind,
                         "disposition_evidence": ("same_employer_notice_multiple_sites_or_phases" if multi_site
                                                  else correspondence or decision.evidence),
                         "filing_group_rows": len(filing_group) if multi_site else None,
                         "filing_group_sites": len(sites_in_group) if multi_site else None,
                         "related_source_row": decision.related_row,
                         "existing_candidate_ids": [prior.get("source_notice_id") or prior.get("id")
                                                    for prior in candidates],
                         "existing_candidate_notice_ids": [prior.get("id") for prior in candidates],
                         "source_row": row["source_row_id"],
                         "source_row_sha256": row["row_sha256"],
                         "source_url": row["source_url"],
                         "notice_year": row["notice_date"][:4], "raw_extra": _json(row)})
            continue
        identity = f"NY:dashboard-observation:{row['row_sha256']}"
        details = {"origin": row["artifact"], "source_row": row["source_row_id"],
                   "source_row_sha256": row["row_sha256"],
                   "source_artifact_sha256": row["artifact_sha256"],
                   "identity_basis": "employer_site_action_or_notice_event_not_filing_id",
                   "disposition": decision.kind, "disposition_evidence": decision.evidence,
                   "agency_posted_date": row["posted_date"],
                   "dashboard_index_not_identity": row["index"],
                   "date_roles": {"Date of WARN Notice": "reported_notice",
                                  "Date Posted": "agency_posting",
                                  "Date Layoff/Closure Starts": "reported_action"},
                   "raw_cells": row["raw_cells"]}
        rec = {"state": "NY", "employer_name": row["company"].strip(),
               "location": row["address"].strip(), "notice_date": row["notice_date"],
               "notice_date_precision": "day", "notice_date_basis": "reported",
               "effective_date": row["effective_date"],
               "effective_date_precision": "day" if row["effective_date"] else None,
               "effective_date_basis": "reported" if row["effective_date"] else None,
               "employees_affected": workers, "layoff_type": "unknown",
               "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": row["source_row_id"],
               "source_identity": identity, "source_details": _json(details),
               "raw_extra": _json(row["raw_cells"]),
               "dedupe_key": hashlib.sha1(identity.encode()).hexdigest()}
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("NY annual source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "held": len(held),
                           "held_existing_employer": 0,
                           "held_existing_notice_correspondence": sum(
                               x["reason"] == "existing_notice_event_correspondence_unresolved" for x in held),
                           "held_multi_site_rows": sum(
                               x["reason"] == "multi_site_filing_identity_unresolved" for x in held),
                           "hold_reasons": dict(Counter(x["reason"] for x in held)),
                           "admitted_missing_action_date": sum(r["effective_date"] is None for r in records),
                           "admitted_missing_workers": sum(r["employees_affected"] is None for r in records)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Add pinned annual NY files to an agency-only bundle deterministically."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("NY annual overlay requires an agency-only base")
    read_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith("agency/ny_annual/") for name in files):
        raise ValueError("base bundle already contains annual NY sources")
    for name in ("manifest.json", *(f"ny_warn_{year}.csv" for year in YEARS)):
        files[f"agency/ny_annual/{name}"] = (artifacts / name).read_bytes()
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
