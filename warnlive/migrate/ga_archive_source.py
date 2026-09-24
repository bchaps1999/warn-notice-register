"""Verified Georgia annual WARN PDFs, parsed without the removed historical CSV."""

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

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _dedupe_key, _record_hash

ID = re.compile(r"GA\d{7,12}")
# These PDF table cells truncate, split, or absorb text in names or places.
# The ID lines remain accounted for; layout-level review is needed before use.
HELD_PARSE_IDS = frozenset({
    "GA201800001", "GA201800008", "GA201800009", "GA201800041", "GA201800044",
    "GA201900016", "GA201900022", "GA201900235", "GA201900727", "GA201900800",
    "GA201900809",
    "GA201900035", "GA201900108", "GA201900146", "GA201900479",
    "GA201900490", "GA201900523", "GA201900639", "GA201900675",
    "GA202000047", "GA202000262", "GA202000269", "GA202000292", "GA202000322",
    "GA202000337", "GA202100004", "GA202100040", "GA202100041", "GA202100046",
    "GA202100062", "GA202100093", "GA202100099", "GA202100119",
})


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _observations(directory: Path) -> list[dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    artifacts = manifest.get("artifacts")
    if (manifest.get("format") != "ga-official-archive-research-v1"
            or not isinstance(artifacts, list) or len(artifacts) != 5
            or {item.get("year") for item in artifacts} != set(range(2018, 2023))):
        raise ValueError("unsupported Georgia archive manifest")
    observations = []
    for item in sorted(artifacts, key=lambda x: x["year"]):
        name = f"{item['year']}-ga-warn-filing-report.pdf"
        path = directory / name
        if item.get("file") != name or path.is_symlink():
            raise ValueError(f"Georgia archive manifest path mismatch: {name}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != item.get("bytes") or digest != item.get("sha256"):
            raise ValueError(f"Georgia archive checksum mismatch: {name}")
        found = []
        with pdfplumber.open(path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text(layout=True) or ""
                lines = text.splitlines()
                for offset, line in enumerate(lines):
                    match = re.match(r"\s*(GA\d{7,12})\b", line)
                    if match:
                        found.append({"id": match.group(1), "year": item["year"],
                                      "page": page_number, "line": offset + 1,
                                      "text": line.strip(), "artifact": f"agency/ga/{name}",
                                      "artifact_sha256": digest, "source_url": item["url"]})
                # Table cells retain multiline company names where the layout
                # text alone does not reliably distinguish city and company.
                for table in page.extract_tables():
                    current = None
                    for row in table:
                        if not row:
                            continue
                        ident = str(row[0] or "").strip()
                        if ID.fullmatch(ident):
                            current = None
                            cells = [(index, " ".join(str(value).split())) for index, value in enumerate(row)
                                     if value and str(value).strip()]
                            if len(cells) != 8:
                                continue
                            city_index = cells[-6][0]
                            current = {"id": ident, "page": page_number,
                                       "fields": [value for _, value in cells],
                                       "company_index_end": city_index,
                                       "continuation": []}
                            for observation in reversed(found):
                                if observation["id"] == ident and observation["page"] == page_number:
                                    observation["table"] = current
                                    break
                        elif current and not ident and len(row) > current["company_index_end"]:
                            tail = row[current["company_index_end"]:]
                            if all(not value or not str(value).strip() for value in tail):
                                words = [" ".join(str(value).split()) for value in row[1:current["company_index_end"]]
                                         if value and str(value).strip()]
                                current["continuation"].extend(words)
        if len(found) != item.get("observed_id_rows"):
            raise ValueError(f"Georgia PDF ID row count changed: {name}")
        observations.extend(found)
    if len({row["id"] for row in observations}) != len(observations):
        raise ValueError("Georgia archive repeats a published ID")
    return observations


def project(directory: Path, existing_ids: set[str] | None = None,
            existing_events: set[tuple[str, str]] | None = None) -> tuple[list[dict], list[dict], dict]:
    """Admit complete PDF table rows with unique GA IDs; hold all other IDs."""
    rows = _observations(directory)
    existing_ids = existing_ids or set()
    existing_events = existing_events or set()
    records, held = [], []
    for row in rows:
        table = row.get("table")
        values = table["fields"] if table else None
        if values:
            company = " ".join([values[1], *table["continuation"]]).strip()
            city, zipcode, county, worker_text, lwda, date_text = values[2:]
            try:
                action = datetime.strptime(date_text, "%m/%d/%Y").date().isoformat()
            except ValueError:
                action = None
            complete = bool(company and city and county and zipcode and worker_text.isdigit()
                            and int(worker_text) > 0 and action)
        else:
            company = city = zipcode = county = worker_text = lwda = date_text = ""
            action = None
            complete = False
        reason = ("pdf_table_row_unresolved" if not complete else
                  "pdf_table_text_unresolved" if row["id"] in HELD_PARSE_IDS else
                  "already_represented_ga_id" if row["id"] in existing_ids else
                  "idless_current_event_overlap" if (company.casefold(), action) in existing_events else None)
        source_row = f"{row['artifact']}:sha256:{row['artifact_sha256']}:page:{row['page']}:id:{row['id']}"
        if reason:
            held.append({"origin": row["artifact"], "state": "GA", "reason": reason,
                         "source_row": source_row, "source_notice_id": row["id"],
                         "source_url": row["source_url"], "notice_year": "unknown",
                         "raw_extra": _json({"line": row["text"], "table": values})})
            continue
        details = {"origin": row["artifact"], "source_row": source_row,
                   "source_artifact_sha256": row["artifact_sha256"],
                   "published_ga_id": row["id"], "pdf_page": row["page"],
                   "pdf_line": row["line"], "pdf_table_values": values,
                   "pdf_company_continuation": table["continuation"],
                   "zipcode": zipcode, "county": county, "lwda": lwda,
                   "date_roles": {"Date": "reported_separation"}}
        rec = {"state": "GA", "employer_name": company,
               "location": f"{city}, {county}", "notice_date": None,
               "effective_date": action, "effective_date_precision": "day",
               "effective_date_basis": "reported", "employees_affected": int(worker_text),
               "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
               "source_url": row["source_url"], "source_notice_id": row["id"],
               "source_identity": f"GA:{row['id']}", "source_details": _json(details),
               "raw_extra": _json(values)}
        rec["dedupe_key"] = _dedupe_key(rec)
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("Georgia archive source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "held": len(held), "hold_reasons": dict(Counter(x["reason"] for x in held))}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Overlay Georgia original PDFs and manifest on an agency-only bundle."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Georgia overlay requires an agency-only base")
    _observations(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith("agency/ga/") for name in files):
        raise ValueError("base bundle already contains Georgia PDF archive")
    for path in sorted(artifacts.iterdir()):
        if path.is_file():
            files[f"agency/ga/{path.name}"] = path.read_bytes()
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
