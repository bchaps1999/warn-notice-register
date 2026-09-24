"""Tennessee agency archive paragraphs, separate from BLN historical rows."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import tarfile
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash

YEARS = ("2021", "2022", "2023", "2024")
LABELS = ("Date Notice Posted", "Company", "County", "Affected Workers",
          "Closure/Layoff Date", "Notice/Type")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _day(value: str) -> str | None:
    value = value.strip()
    for pattern in ("%m/%d/%Y", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if (manifest.get("format") != "warn-tn-agency-archive-v1" or
            manifest.get("artifact") != "reports.html"):
        raise ValueError("unsupported Tennessee source manifest")
    content = (directory / "reports.html").read_bytes()
    if len(content) != manifest.get("bytes") or hashlib.sha256(content).hexdigest() != manifest.get("sha256"):
        raise ValueError("Tennessee archive checksum mismatch")
    soup = BeautifulSoup(content, "html.parser")
    rows = []
    counts: Counter[str] = Counter()
    for section in soup.select(".accordion-item"):
        heading = section.select_one(".accordion-button")
        year = heading.get_text(" ", strip=True) if heading else ""
        if year not in YEARS:
            continue
        for ordinal, paragraph in enumerate(section.select(".accordion-body p"), start=1):
            original = paragraph.get_text(" ", strip=True)
            parts = [part.strip() for part in original.split("|")]
            fields = {}
            if len(parts) == len(LABELS):
                for label, part in zip(LABELS, parts, strict=True):
                    prefix = f"{label}:"
                    if part.startswith(prefix):
                        fields[label] = part[len(prefix):].strip()
            hrefs = [urljoin(manifest["source_url"], a["href"])
                     for a in paragraph.select("a[href]")]
            rows.append({"section_year": year, "section_row": ordinal,
                         "source_row": f"{year}:p{ordinal}", "source_text": original,
                         "fields": fields, "document_urls": hrefs,
                         "source_row_sha256": hashlib.sha256(original.encode()).hexdigest()})
            counts[year] += 1
    if dict(counts) != manifest.get("archived_paragraph_rows"):
        raise ValueError("Tennessee archive row count drift")
    return rows, manifest


def project(directory: Path, current_raw: Path | None = None) -> tuple[list[dict], list[dict], dict]:
    rows, manifest = read_artifacts(directory)
    ids = Counter((row["section_year"], row["fields"].get("Notice/Type", "")) for row in rows)
    current_ids = set()
    if current_raw is not None:
        with Path(current_raw).open(newline="", encoding="utf-8-sig") as stream:
            current_ids = {(row.get("Notice ID") or "").lstrip("# ").strip()
                           for row in csv.DictReader(stream)}
    records, held = [], []
    for row in rows:
        fields = row["fields"]
        year, number = row["section_year"], fields.get("Notice/Type", "")
        number = number.lstrip("# ").strip()
        posting = _day(fields.get("Date Notice Posted", ""))
        workers_text = fields.get("Affected Workers", "")
        company = fields.get("Company", "").strip()
        required = set(LABELS) - {"County"}
        complete = (required.issubset(fields) and bool(re.fullmatch(r"20\d{6,7}", number))
                    and ids[(year, fields.get("Notice/Type", ""))] == 1
                    and number not in current_ids
                    and posting is not None and posting.startswith(year)
                    and bool(company) and company.lower() != "view"
                    and bool(re.fullmatch(r"\d+(?:,\d{3})*", workers_text)))
        if not complete:
            held.append({"origin": "agency/tn/reports.html", "state": "TN",
                         "reason": ("overlaps_current_source_notice_number" if number in current_ids
                                    else "agency_archive_row_ambiguous"),
                         "source_row": row["source_row"],
                         "source_row_sha256": row["source_row_sha256"],
                         "source_notice_id": number or None, "source_url": manifest["source_url"],
                         "notice_year": None, "raw_extra": _json(row)})
            continue
        effective = _day(fields["Closure/Layoff Date"])
        identity = f"TN:agency:{year}:{number}"
        details = {"origin": "agency/tn/reports.html", "source_row": row["source_row"],
                   "source_row_sha256": row["source_row_sha256"],
                   "source_html_sha256": manifest["sha256"],
                   "identity_basis": "archive_section_and_notice_number",
                   "agency_posted_date": posting,
                   "date_roles": {"Date Notice Posted": "agency_posting",
                                  "Closure/Layoff Date": "reported_action"},
                   "reported_action_text": fields["Closure/Layoff Date"],
                   "document_urls": row["document_urls"], "source_text": row["source_text"]}
        rec = {"state": "TN", "employer_name": company,
               "location": fields.get("County") or None, "notice_date": None,
               "effective_date": effective, "employees_affected": int(workers_text.replace(",", "")),
               "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
               "source_url": manifest["source_url"], "source_notice_id": number,
               "source_identity": identity, "source_details": _json(details),
               "raw_extra": _json(row),
               "dedupe_key": hashlib.sha1(identity.encode()).hexdigest()}
        if effective:
            rec.update(effective_date_precision="day", effective_date_basis="reported")
        rec["raw_record_hash"] = _record_hash(rec)
        records.append(rec)
    if len(records) + len(held) != len(rows):
        raise ValueError("Tennessee archive source row accounting mismatch")
    return records, held, {"source_rows": len(rows), "admitted": len(records),
                           "held": len(held)}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base_manifest = verify(base_bundle)
    if base_manifest.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Tennessee overlay requires an agency-only base")
    project(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith("agency/tn/") for name in files):
        raise ValueError("base bundle already contains Tennessee archive")
    for name in ("manifest.json", "reports.html"):
        files[f"agency/tn/{name}"] = (artifacts / name).read_bytes()
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
