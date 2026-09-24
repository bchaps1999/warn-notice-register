"""Pinned, rendered Missouri agency annual WARN tables (2019–24)."""

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

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash

PREFIX = "agency/mo_annual"
YEARS = range(2019, 2025)
COUNTS = {2019: 26, 2020: 162, 2021: 22, 2022: 10, 2023: 37, 2024: 40}
EXPECTED_SHA256 = {
    2019: "8b3db262bb269098c9ecf5812f5502383087777928ef999126bb25e649041e7e",
    2020: "9f7fa355c09c898ca7317ae5b933f601ac9ebd27b963d854f834a8efc4be5ed4",
    2021: "ea9c686a7f920af5f8221d47234a0f0413fe433a856bf2a905ec848202dd210b",
    2022: "91d76598f69efbce723d57822f7c1a91b65b8eebe8ecc6dea5a073f2a5f54c7f",
    2023: "ff7135591aec402ca7b29832d6582a5b664832681a242327ec2fc87d9a8feca3",
    2024: "69e32cd69ace2f0ff882ded9a445f4146117020bd4927d6072985763bf5bdb29",
}
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")
LINE = re.compile(r"^L(\d+): ?(.*)$")


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _dates(value: str) -> list[str] | None:
    result = []
    for candidate in DATE.findall(value):
        try:
            result.append(datetime.strptime(candidate, "%m/%d/%Y").date().isoformat())
        except ValueError:
            return None
    return result


def _usable_action(raw: dict) -> str | None:
    dates = _dates(raw["action_dates"])
    return (dates[0] if dates and len(dates) == 1
            and "tbd" not in raw["notes"].casefold()
            and 1990 <= int(dates[0][:4]) <= 2030 else None)


def read_artifacts(directory: Path) -> tuple[list[dict], dict]:
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    specs = manifest.get("files")
    if (manifest.get("format") != "agency-rendered-annual-warn-text-v1"
            or manifest.get("source_rows") != 297 or not isinstance(specs, list)
            or [x.get("year") for x in specs] != list(YEARS)):
        raise ValueError("unsupported Missouri annual source manifest")
    rows = []
    for spec in specs:
        year = spec["year"]
        name = f"{year}.webtext.txt"
        path = directory / name
        if (path.is_symlink() or spec.get("file") != name
                or spec.get("agency_table_rows") != COUNTS[year]
                or spec.get("sha256") != EXPECTED_SHA256[year]
                or spec.get("source_url") != f"https://jobs.mo.gov/warn/{year}"):
            raise ValueError(f"Missouri annual source manifest changed: {year}")
        content = path.read_bytes()
        if (len(content) != spec["bytes"]
                or hashlib.sha256(content).hexdigest() != spec["sha256"]):
            raise ValueError(f"Missouri annual source checksum mismatch: {year}")
        lines = []
        for source_line in content.decode().replace(" L21:", "\nL21:").splitlines():
            match = LINE.match(source_line)
            if match:
                lines.append((int(match[1]), match[2]))
        by_line = dict(lines)
        if (max(by_line) + 1 != spec["rendered_source_lines"]
                or [number for number, _ in lines if number >= 20]
                != list(range(20, spec["rendered_source_lines"]))
                or not by_line[20].startswith("cite2†Received")
                or "# affected" not in by_line[20]):
            raise ValueError(f"Missouri annual source lines changed: {year}")
        width = by_line[20].count("|") + 1
        if width != (8 if year < 2021 else 9 if year == 2022 else 10):
            raise ValueError(f"Missouri annual table width changed: {year}")
        pending = []
        groups = []
        for number, value in lines:
            if number < 22:
                continue
            pending.append((number, value))
            separators = sum(part.count("|") for _, part in pending)
            if separators > width - 1:
                raise ValueError(f"Missouri annual row width overflow: {year}:{number}")
            if separators == width - 1:
                cells = [part.strip() for part in
                         "\n".join(part for _, part in pending).split("|")]
                if len(cells) != width:
                    raise ValueError(f"Missouri annual row width changed: {year}:{number}")
                groups.append((pending[0][0], number, cells))
                pending = []
        if pending or len(groups) != COUNTS[year] + 1 or any(groups[-1][2][:-2]):
            raise ValueError(f"Missouri annual row accounting changed: {year}")
        for ordinal, (start, end, cells) in enumerate(groups[:-1], start=1):
            if not cells[1]:
                raise ValueError(f"Missouri annual employer missing: {year}:{ordinal}")
            raw = {"received": cells[0], "title": cells[1],
                   "industry": cells[2] if width >= 9 else "",
                   "locations": cells[3] if width >= 9 else cells[2],
                   "county": cells[4] if width >= 9 else cells[3],
                   "region": cells[5] if width >= 9 else cells[4],
                   "type": cells[6] if width >= 9 else cells[5],
                   "action_dates": cells[-3] if width == 10 else cells[-2],
                   "affected": cells[-2] if width == 10 else cells[-1],
                   "notes": cells[-1] if width == 10 else ""}
            rows.append({"year": year, "ordinal": ordinal, "line_start": start,
                         "line_end": end, "raw": raw,
                         "source_row_sha256": hashlib.sha256(_json(raw).encode()).hexdigest(),
                         "artifact_sha256": spec["sha256"], "source_url": spec["source_url"]})
    if len(rows) != 297:
        raise ValueError("Missouri annual total row accounting changed")
    return rows, manifest


def project(directory: Path, existing_events: set[tuple[str, str | None]] | None = None
            ) -> tuple[list[dict], list[dict], dict]:
    rows, _ = read_artifacts(directory)
    existing_events = existing_events or set()
    signatures = Counter()
    for row in rows:
        raw = row["raw"]
        received = _dates(raw["received"])
        action = _usable_action(raw)
        signatures[(raw["title"].casefold(), action,
                    received[0] if received else None,
                    raw["locations"].casefold())] += 1
    records, held = [], []
    for row in rows:
        raw = row["raw"]
        received = _dates(raw["received"])
        employer = raw["title"].strip()
        action = _usable_action(raw)
        location = ", ".join(x for x in (raw["locations"], raw["county"]) if x).strip() or None
        workers = int(raw["affected"]) if raw["affected"].isdigit() else None
        signature = (employer.casefold(), action,
                     received[0] if received else None, raw["locations"].casefold())
        reason = ("out_of_state_location" if re.search(r"\bKS\b|Kansas$", raw["locations"]) else
                  "zero_affected_no_event" if workers == 0 else
                  "duplicate_annual_event" if signatures[signature] != 1 else
                  "current_agency_event_overlap" if action and
                  (employer.casefold(), action) in existing_events else None)
        pointer = (f"{PREFIX}/{row['year']}.webtext.txt:sha256:{row['artifact_sha256']}:"
                   f"lines:{row['line_start']}-{row['line_end']}:row:{row['ordinal']}")
        if reason:
            held.append({"origin": f"{PREFIX}/{row['year']}.webtext.txt", "state": "MO",
                         "reason": reason, "source_row": pointer,
                         "source_row_sha256": row["source_row_sha256"],
                         "source_notice_id": None, "source_url": row["source_url"],
                         "notice_year": row["year"], "raw_extra": _json(raw)})
            continue
        details = {"origin": f"{PREFIX}/{row['year']}.webtext.txt",
                   "source_row": pointer, "source_row_sha256": row["source_row_sha256"],
                   "source_artifact_sha256": row["artifact_sha256"],
                   "identity_basis": "agency_annual_table_row_no_filing_id",
                   "agency_received_dates": received,
                   "date_roles": {"Received": "agency_receipt",
                                  "Layoff date(s)": "reported_action"},
                   "raw_fields": raw}
        kind = raw["type"].casefold()
        record = {"state": "MO", "employer_name": employer,
                  "location": location, "notice_date": None,
                  "effective_date": action,
                  "effective_date_precision": "day" if action else None,
                  "effective_date_basis": "reported" if action else None,
                  "employees_affected": workers,
                  "layoff_type": "closure" if "clos" in kind else
                                 "mass_layoff" if "layoff" in kind else "unknown",
                  "is_temporary": True if "temporary layoff" in raw["notes"].casefold() else None,
                  "is_amendment": 0,
                  "source_url": row["source_url"], "source_notice_id": None,
                  "source_identity": None, "source_details": _json(details),
                  "raw_extra": _json(raw),
                  "dedupe_key": hashlib.sha1(pointer.encode()).hexdigest()}
        record["raw_record_hash"] = _record_hash(record)
        records.append(record)
    if len(records) + len(held) != 297:
        raise ValueError("Missouri annual source row accounting mismatch")
    return records, held, {"source_rows": 297, "admitted": len(records),
                           "held": len(held),
                           "hold_reasons": dict(Counter(item["reason"] for item in held))}


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Overlay exact annual source captures on an agency-only replay bundle."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Missouri annual overlay requires an agency-only base")
    read_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith(PREFIX + "/") for name in files):
        raise ValueError("base bundle already contains Missouri annual source")
    for name in ("manifest.json", *(f"{year}.webtext.txt" for year in YEARS)):
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
