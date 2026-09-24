"""Project the pinned Kentucky Career Center report without inventing notice dates.

The current linked CSV is a snapshot, not a complete annual inventory. Its
notice numbers identify agency rows; ``Date Received`` is an agency receipt
day, while ``Projected Date`` describes the planned action.
"""

from __future__ import annotations

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
from urllib.parse import urlparse

from warnlive.migrate.source_bundle import _entry, verify
from warnlive.normalize.engine import _record_hash


PREFIX = "agency/ky"
FILENAME = "WARN-Report-2026-09-16.csv"
SHA256 = "a40a050d1b10a2ea4d1ec54302d548c4499534d1e16411840b9c383d5d2b2da5"
HEADERS = (
    "Company: Company Name", "Notice Type", "Notice: Notice Number",
    "Closure or Layoff?", "County", "Date Received", "NAICS",
    "Notice URL", "Number of Employees Affected", "Projected Date",
    "Trade", "Type of Employees Affected", "Workforce Board",
)
NOTICE_ID = re.compile(r"Notice ([1-9][0-9]*)\Z")
COUNT = 35
IN_STATE = 33
OUT_OF_STATE = {"2833", "2778"}


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _date(value: str, field: str, row_number: int) -> str:
    if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        raise ValueError(f"invalid Kentucky {field} at row {row_number}: {value!r}")
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid Kentucky {field} at row {row_number}: {value!r}") from exc


def _artifact(directory: Path, name: str) -> Path:
    path = directory / name
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError(f"invalid Kentucky source artifact: {path}")
    return path


def read_artifacts(directory: Path) -> list[dict]:
    """Verify source bytes and every physical CSV row against the pinned manifest."""
    directory = Path(directory)
    manifest = json.loads(_artifact(directory, "manifest.json").read_text())
    if (manifest.get("format") != "ky-agency-report-capture-v1"
            or manifest.get("file") != FILENAME
            or manifest.get("sha256") != SHA256
            or manifest.get("rows") != COUNT
            or manifest.get("in_state_rows") != IN_STATE
            or manifest.get("unique_notice_numbers") != COUNT
            or manifest.get("observed_utc_date") != "2026-09-24"
            or manifest.get("source_page") != "https://kcc.ky.gov/Pages/News.aspx"
            or manifest.get("source_url") != (
                "https://kcc.ky.gov/WARN%20notices/WARN%20Notices%202026/"
                "WARN%20Report-2026-09-16-09-00-01.csv")
            or not isinstance(manifest.get("out_of_state_hold_rows"), list)):
        raise ValueError("Kentucky source manifest changed")
    path = _artifact(directory, FILENAME)
    content = path.read_bytes()
    if len(content) != manifest.get("bytes") or hashlib.sha256(content).hexdigest() != SHA256:
        raise ValueError("Kentucky source checksum mismatch")
    with io.StringIO(content.decode("utf-8-sig"), newline="") as stream:
        reader = csv.reader(stream)
        if tuple(next(reader, ())) != HEADERS:
            raise ValueError("Kentucky source header changed")
        observations = []
        ids: set[str] = set()
        urls: set[str] = set()
        for row_number, cells in enumerate(reader, start=2):
            if len(cells) != len(HEADERS):
                raise ValueError(f"Kentucky CSV row width changed: {row_number}")
            raw = dict(zip(HEADERS, cells, strict=True))
            match = NOTICE_ID.fullmatch(raw["Notice: Notice Number"])
            if not match or match.group(1) in ids:
                raise ValueError(f"invalid or duplicate Kentucky notice ID at row {row_number}")
            ident = match.group(1)
            ids.add(ident)
            document = raw["Notice URL"]
            parsed = urlparse(document)
            if (parsed.scheme != "https" or parsed.netloc != "kydev.my.salesforce.com"
                    or not parsed.path.startswith("/sfc/p/") or document in urls):
                raise ValueError(f"invalid or duplicate Kentucky notice URL at row {row_number}")
            urls.add(document)
            if (not raw["Company: Company Name"].strip()
                    or raw["Notice Type"] != "WARN Notice"
                    or not raw["County"].strip()
                    or raw["Closure or Layoff?"] not in {"", "Closure", "Layoff"}
                    or not raw["Number of Employees Affected"].isdigit()
                    or int(raw["Number of Employees Affected"]) <= 0):
                raise ValueError(f"incomplete Kentucky source row {row_number}")
            received = _date(raw["Date Received"], "Date Received", row_number)
            projected = _date(raw["Projected Date"], "Projected Date", row_number)
            if received[:4] not in {"2025", "2026"} or projected[:4] != "2026":
                raise ValueError(f"Kentucky source date scope changed at row {row_number}")
            raw_hash = hashlib.sha256(_json(raw).encode()).hexdigest()
            observations.append({
                "source_artifact": f"{PREFIX}/{FILENAME}",
                "source_artifact_sha256": SHA256,
                "source_row": f"{PREFIX}/{FILENAME}:sha256:{SHA256}:row:{row_number}:sha256:{raw_hash}",
                "source_row_sha256": raw_hash,
                "source_url": manifest["source_url"],
                "document_url": document,
                "row_number": row_number,
                "source_notice_id": ident,
                "agency_received_date": received,
                "projected_date": projected,
                "workers_reported": int(raw["Number of Employees Affected"]),
                "company_text": raw["Company: Company Name"],
                "county_text": raw["County"],
                "effective_date": projected,
                "raw": raw,
                "raw_fields": raw,
            })
    if len(observations) != COUNT:
        raise ValueError("Kentucky source row count changed")
    held = [row for row in observations if row["raw_fields"]["County"] == "Out of the State County"]
    expected_holds = {(
        item.get("notice_id"), item.get("employer"), item.get("county"), item.get("employees"))
        for item in manifest["out_of_state_hold_rows"]}
    actual_holds = {(
        f"Notice {row['source_notice_id']}", row["raw_fields"]["Company: Company Name"],
        row["raw_fields"]["County"], row["raw_fields"]["Number of Employees Affected"])
        for row in held}
    if (len(held) != COUNT - IN_STATE or
            {row["source_notice_id"] for row in held} != OUT_OF_STATE or
            expected_holds != actual_holds):
        raise ValueError("Kentucky out-of-state row boundary changed")
    return observations


def project(directory: Path) -> tuple[list[dict], list[dict], dict]:
    """Admit in-state agency IDs and account for the two out-of-state rows."""
    rows = read_artifacts(directory)
    admitted: list[dict] = []
    held: list[dict] = []
    for row in rows:
        raw = row["raw_fields"]
        if raw["County"] == "Out of the State County":
            held.append({
                "origin": row["source_artifact"], "state": "KY",
                "reason": "source_explicitly_out_of_state",
                "source_row": row["source_row"],
                "source_row_sha256": row["source_row_sha256"],
                "source_notice_id": row["source_notice_id"],
                "source_url": row["source_url"],
                "document_url": row["document_url"],
                "notice_year": row["agency_received_date"][:4],
                "raw_extra": _json(raw),
            })
            continue
        identity = f"KY:{row['source_notice_id']}"
        kind = raw["Closure or Layoff?"]
        details = {
            "source_artifact": row["source_artifact"],
            "source_artifact_sha256": row["source_artifact_sha256"],
            "source_row": row["source_row"],
            "source_row_sha256": row["source_row_sha256"],
            "source_document_url": row["document_url"],
            "identity_basis": "agency_notice_number",
            "agency_received_date": row["agency_received_date"],
            "projected_action_date": row["projected_date"],
            "date_roles": {"Date Received": "agency_receipt",
                           "Projected Date": "projected_action"},
            "date_evidence_rule": "ky_agency_report_labeled_fields_v1",
            "location_basis": "agency_county_field",
            "raw_fields": raw,
        }
        record = {
            "state": "KY", "employer_name": raw["Company: Company Name"].strip(),
            "location": raw["County"].strip(),
            "notice_date": None,
            "effective_date": row["projected_date"],
            "effective_date_precision": "day", "effective_date_basis": "reported",
            "employees_affected": row["workers_reported"],
            "layoff_type": "closure" if kind == "Closure" else "mass_layoff" if kind == "Layoff" else "unknown",
            "is_temporary": None, "is_amendment": 0,
            "source_url": row["source_url"],
            "source_notice_id": row["source_notice_id"],
            "source_identity": identity,
            "source_details": _json(details),
            "raw_extra": _json(raw),
            "dedupe_key": hashlib.sha1(f"KY|source|{identity}".encode()).hexdigest(),
        }
        record["raw_record_hash"] = _record_hash(record)
        admitted.append(record)
    if len(admitted) != IN_STATE or len(admitted) + len(held) != COUNT:
        raise ValueError("Kentucky source row accounting mismatch")
    return admitted, held, {
        "source_rows": COUNT, "admitted": len(admitted), "held": len(held),
        "hold_reasons": dict(Counter(item["reason"] for item in held)),
        "admitted_workers": sum(item["employees_affected"] for item in admitted),
    }


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Add the exact pinned agency CSV and manifest to an agency-only bundle."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base = verify(base_bundle)
    if base.get("admission_inputs") != "agency-only-v1":
        raise ValueError("Kentucky overlay requires an agency-only base")
    read_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
    if any(name.startswith(PREFIX + "/") for name in files):
        raise ValueError("base bundle already contains Kentucky source")
    if "raw/ky.csv" in files:
        raise ValueError("base bundle still contains unverified Kentucky raw capture")
    for name in ("manifest.json", FILENAME):
        files[f"{PREFIX}/{name}"] = (artifacts / name).read_bytes()
    manifest = {**base, "files": [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in sorted(files.items())]}
    out_bundle.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_bundle.open("xb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", filename="", mtime=0
        ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            header = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
            for name, content in sorted(files.items()):
                archive.addfile(_entry(name, content), io.BytesIO(content))
        verify(out_bundle)
    except BaseException:
        out_bundle.unlink(missing_ok=True)
        raise
    return manifest
