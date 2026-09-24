"""Pinned Ohio agency WARN annual tables, 2015–22.

The agency's PDF and archived HTML tables contain received dates, not notice
letter dates.  Each physical table row is retained as an observation; repeated
Notice IDs are reconciled without manufacturing additional notices.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path

import pdfplumber
from bs4 import BeautifulSoup

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash
from warnlive.normalize.revisions import classify_agency_ids

PREFIX = "agency/oh_annual"
YEARS = range(2015, 2023)
COUNTS = {2015: 65, 2016: 109, 2017: 92, 2018: 68, 2019: 100,
          2020: 376, 2021: 29, 2022: 59}
SHA256 = {
    2015: "6ac46aa5ec9e7f06c0a62267338f96054699a141377d7f3e11240831e65d094f",
    2016: "8d82c62af53a6363827dc18aac1fc3c3818f9b5984454eb8b13a7dfe41e2af5a",
    2017: "2b15fa6cea5f5520ed5c6855d89ee5d5fa9b719db7e3132c5404061345f18e5d",
    2018: "4f3a7404faf917da5e26276ba228b64b138f6e22b8659377e8fef454039755ac",
    2019: "5aaf1adfa2852bc2158730d24d95e2a43f0aa6d3ad4698c6d14b67193b7b72f8",
    2020: "5437fa56779f27ecf5df4ce1df98fd4ab00ab48ac8c3263e3c0e37f6648e65fe",
    2021: "0f7a1689233dd94267f0d847244e9613939d0d70e6df2f2a4d115531c31d24f5",
    2022: "52d9f3ece5a102184061befe8deeda727590299e8ea5511999d80bef38c3fad9",
}
CAPTURE_URLS = {
    2015: "https://web.archive.org/web/20200510102316id_/http://jfs.ohio.gov/warn/WARN2015.stm",
    2016: "https://web.archive.org/web/20200510102317id_/http://jfs.ohio.gov/warn/WARN2016.stm",
    2017: "https://web.archive.org/web/20200510102313id_/http://jfs.ohio.gov/warn/WARN2017.stm",
    2018: "https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN2018.pdf",
    2019: "https://dam.assets.ohio.gov/image/upload/jfs.ohio.gov/warn/WARN2019.pdf",
    2020: "https://web.archive.org/web/20210429023021id_/https://jfs.ohio.gov/warn/archive.stm?year=2020",
    2021: "https://web.archive.org/web/20220521120840id_/https://jfs.ohio.gov/warn/archive.stm?year=2021",
    2022: "https://web.archive.org/web/20230504193636id_/https://jfs.ohio.gov/warn/archive.stm?year=2022",
}
PDF_PAGES = {2015: 6, 2016: 9, 2017: 4, 2018: 4, 2019: 5}
ID = re.compile(r"(?:Updated\s+)?(\d{3}-\d{2}-\d{2,3})\*?", re.I)
DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
FIELDS = ("received", "company", "city_county", "affected", "layoff_dates",
          "phone", "union", "notice_id")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _date(value: str) -> str | None:
    matches = DATE.findall(value)
    if len(matches) != 1:
        return None
    try:
        return datetime.strptime(matches[0], "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def _action(value: str) -> str | None:
    # A range, schedule or narrative is not a single action date.
    if not re.fullmatch(r"\s*\d{1,2}/\d{1,2}/\d{4}\s*", value):
        return None
    return _date(value)


def _pdf_rows(path: Path, year: int) -> list[tuple[int, int, list[str], list[dict]]]:
    result = []
    with pdfplumber.open(path) as pdf:
        if len(pdf.pages) != PDF_PAGES[year]:
            raise ValueError(f"Ohio PDF page count changed: {year}")
        for page_number, page in enumerate(pdf.pages, 1):
            tables = page.extract_tables()
            if len(tables) != 1:
                raise ValueError(f"Ohio PDF table count changed: {year}:{page_number}")
            table = tables[0]
            if not table or any(len(cells) != 8 for cells in table):
                raise ValueError(f"Ohio PDF table layout changed: {year}:{page_number}")
            # The 2015 PDF repeats its header only on page one.
            has_header = str(table[0][0]).startswith(f"{year} WARN Notices")
            if year != 2015 and not has_header:
                raise ValueError(f"Ohio PDF title changed: {year}:{page_number}")
            if has_header and (len(table) < 3 or table[1][1] != "Company" or table[1][-1] != "WARN ID"):
                raise ValueError(f"Ohio PDF header changed: {year}:{page_number}")
            for table_row, cells in enumerate(table[2:] if has_header else table, 3 if has_header else 1):
                if len(cells) != 8:
                    raise ValueError(f"Ohio PDF row width changed: {year}:{page_number}:{table_row}")
                cells = [str(x or "") for x in cells]
                if not cells[-1]:
                    if not result:
                        raise ValueError(f"orphan Ohio PDF continuation: {year}:{page_number}:{table_row}")
                    result[-1][3].append({"page": page_number, "table_row": table_row,
                                          "cells": cells})
                else:
                    result.append((page_number, table_row, cells, []))
    return result


def _html_rows(path: Path, year: int) -> list[tuple[int, int, list[str]]]:
    soup = BeautifulSoup(path.read_bytes(), "html.parser")
    tables = [t for t in soup.find_all("table") if len(t.find_all("tr")) > 10]
    if len(tables) != 1:
        raise ValueError(f"Ohio HTML table count changed: {year}")
    rows = tables[0].find_all("tr")
    header = [c.get_text(" ", strip=True) for c in rows[0].find_all(["th", "td"])]
    if header != ["Date Received", "Company", "City/County", "Potential Number Affected",
                  "Layoff Date(s)", "Phone Number", "Union", "Notice ID"]:
        raise ValueError(f"Ohio HTML header changed: {year}")
    result = []
    for table_row, tr in enumerate(rows[1:], 2):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) != 8:
            raise ValueError(f"Ohio HTML row width changed: {year}:{table_row}")
        result.append((1, table_row, cells))
    return result


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = manifest.get("files")
    if (manifest.get("format") != "oh-agency-annual-source-v1" or
            not isinstance(specs, list) or [s.get("year") for s in specs] != list(YEARS)):
        raise ValueError("unsupported Ohio annual source manifest")
    result = []
    for spec in specs:
        year = spec["year"]
        suffix = "pdf" if year <= 2019 else "html"
        name = f"WARN{year}.{suffix}"
        path = directory / name
        if (path.is_symlink() or spec.get("file") != name or spec.get("format") != suffix
                or spec.get("sha256") != SHA256[year]
                or spec.get("capture_url") != CAPTURE_URLS[year]
                or (year <= 2019 and spec.get("pages") != PDF_PAGES[year])
                or "jfs.ohio.gov/warn/" not in spec.get("original_agency_url", "")):
            raise ValueError(f"Ohio annual manifest changed: {year}")
        content = path.read_bytes()
        if len(content) != spec.get("bytes") or hashlib.sha256(content).hexdigest() != SHA256[year]:
            raise ValueError(f"Ohio annual source checksum mismatch: {year}")
        observations = _pdf_rows(path, year) if suffix == "pdf" else _html_rows(path, year)
        if len(observations) != COUNTS[year]:
            raise ValueError(f"Ohio annual row count changed: {year}")
        for ordinal, observation in enumerate(observations, 1):
            page, table_row, cells, continuations = observation if suffix == "pdf" else (*observation, [])
            raw = dict(zip(FIELDS, cells, strict=True))
            raw["continuation_rows"] = continuations
            result.append({"year": year, "ordinal": ordinal, "page": page,
                           "table_row": table_row, "raw": raw,
                           "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest(),
                           "artifact_sha256": SHA256[year],
                           "source_url": spec["capture_url"], "file": name})
    if len(result) != sum(COUNTS.values()):
        raise ValueError("Ohio annual total row accounting changed")
    return result, manifest


def project(directory: Path, existing_ids: set[str] | None = None
            ) -> tuple[list[dict], list[dict], dict]:
    rows, _ = read_artifacts(directory)
    existing_ids = {str(x).removeprefix("OH:") for x in (existing_ids or set())}
    for row in rows:
        raw_id = row["raw"]["notice_id"].strip().replace("‐", "-")
        match = ID.fullmatch(raw_id)
        row["id"] = match.group(1) if match else None
        row["updated_id"] = bool(re.search(r"\bUpdated\b", raw_id, re.I))
        row["amended"] = row["updated_id"] or bool(re.search(
            r"\bUpdated\b|\bRevised\b", row["raw"]["received"], re.I)) or any(
                re.search(r"\bUpdated\b|\bRevised\b", continuation["cells"][0], re.I)
                for continuation in row["raw"]["continuation_rows"])
    def pointer(row: dict) -> str:
        return (f"{PREFIX}/{row['file']}:sha256:{row['artifact_sha256']}:page:{row['page']}:"
                f"table_row:{row['table_row']}:ordinal:{row['ordinal']}")

    decisions = classify_agency_ids(
        rows, row_key=pointer, agency_id=lambda r: r["id"],
        site=lambda r: r["raw"]["city_county"].casefold().strip(),
        action=lambda r: _action(r["raw"]["layoff_dates"]),
        revision=lambda r: r["amended"], content=lambda r: _json(r["raw"]),
        preferred=lambda r: not r["updated_id"],
    )
    revisions_by_original: dict[str, list[str]] = {}
    for source_row, decision in decisions.items():
        if decision.kind == "revision" and decision.related_row:
            revisions_by_original.setdefault(decision.related_row, []).append(source_row)
    records, held = [], []
    for row in rows:
        raw = row["raw"]
        ident = row["id"]
        additional_sites = any(
            continuation["cells"][2].strip() or continuation["cells"][3].strip()
            for continuation in raw["continuation_rows"])
        origin = f"{PREFIX}/{row['file']}"
        source_pointer = pointer(row)
        decision = decisions[source_pointer]
        reason = ("invalid_or_multiple_notice_id" if not ident else
                  "already_represented_oh_id" if ident in existing_ids else
                  "conflicting_original_notice_id" if decision.kind == "unresolved" else
                  "superseding_amendment_observation" if decision.kind == "revision" else
                  "duplicate_agency_capture" if decision.kind == "duplicate_capture" else
                  "multiple_sites_in_source_row" if additional_sites else
                  "missing_employer" if not raw["company"].strip() else
                  "missing_location" if not raw["city_county"].strip() else None)
        if reason:
            held.append({"origin": origin, "state": "OH", "reason": reason,
                         "source_row": source_pointer, "source_row_sha256": row["source_row_sha256"],
                         "disposition": "unresolved" if decision.kind == "notice" else decision.kind,
                         "preliminary_disposition": decision.kind,
                         "disposition_evidence": decision.evidence,
                         "related_source_row": decision.related_row,
                         "source_notice_id": ident, "source_url": row["source_url"],
                         "notice_year": row["year"], "raw_extra": _json(raw)})
            continue
        affected = raw["affected"].strip().replace(",", "")
        workers = int(affected) if affected.isdigit() else None
        action = _action(raw["layoff_dates"])
        details = {"origin": origin, "source_row": source_pointer,
                   "source_row_sha256": row["source_row_sha256"],
                   "source_artifact_sha256": row["artifact_sha256"],
                   "identity_basis": "agency_WARN_or_Notice_ID",
                   "disposition": decision.kind, "disposition_evidence": decision.evidence,
                   "related_revision_source_rows": sorted(revisions_by_original.get(source_pointer, [])),
                   "agency_received_date": _date(raw["received"]),
                   "date_roles": {"Date Received": "agency_receipt",
                                  "Layoff Date(s)": "reported_action"},
                   "raw_fields": raw}
        rec = {"state": "OH", "employer_name": raw["company"].strip().replace("\n", " "),
               "location": raw["city_county"].strip().replace("\n", " "),
               "notice_date": None, "effective_date": action,
               "effective_date_precision": "day" if action else None,
               "effective_date_basis": "reported" if action else None,
               "employees_affected": workers, "layoff_type": "unknown",
               "is_temporary": None, "is_amendment": int(row["amended"]),
               "source_url": row["source_url"], "source_notice_id": ident,
               "source_identity": f"OH:{ident}", "source_details": _json(details),
               "raw_extra": _json(raw),
               "dedupe_key": hashlib.sha1(f"OH|source|{ident}".encode()).hexdigest()}
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != sum(COUNTS.values()):
        raise ValueError("Ohio annual source row accounting mismatch")
    return records, held, {"source_rows": sum(COUNTS.values()), "admitted": len(records),
                           "held": len(held),
                           "hold_reasons": dict(Counter(x["reason"] for x in held)),
                           "admitted_missing_workers": sum(x["employees_affected"] is None for x in records),
                           "admitted_missing_action_date": sum(x["effective_date"] is None for x in records),
                           "revision_observations": sum(x["disposition"] == "revision" for x in held),
                           "duplicate_captures": sum(x["disposition"] == "duplicate_capture" for x in held)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Overlay exact Ohio agency artifacts on an agency-only replay bundle."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Ohio annual overlay requires an agency-only base")
    read_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith(PREFIX + "/") for name in files):
        raise ValueError("base bundle already contains Ohio annual source")
    for name in ("manifest.json", *(f"WARN{y}.{'pdf' if y <= 2019 else 'html'}" for y in YEARS)):
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
