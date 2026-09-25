"""Attach verified notice facts from frozen official source documents.

This pass is repeatable after either an offline replay or a live scrape.  It
does not create notices or change source-derived keys.  Unknown and ambiguous
matches remain unfilled and are counted in the returned report.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from warnlive.enrich.ca_address import CaMatcher, collect_records
from warnlive.enrich.places import Resolver
from warnlive.enrich.site_address import _STATE_NAMES, clean_address
from warnlive.store.dedupe import append_repair_version

DEFAULT_ROOT = Path("data/source_snapshots/2026-09-24-quality-evidence")
CA_BASE_URL = "https://edd.ca.gov/siteassets/files/jobs_and_training/warn/"

_VOLTA = {
    "a2d896b37927bbf3c3d69df9dfcaf5a087aa2917": ("WI", "San Francisco", "Lake Geneva"),
    "11bd04f54d8dab275593a488a606455d464e87cd": ("FL", "COOPER CITY", "Cooper City"),
    "4fddabd36225505badda369799e0234662ade790": ("FL", "MIAMI", "Miami"),
    "f16b9618e4a2f8535dfde6b79c5788cde3c0db17": ("FL", "JACKSONVILLE BEACH", "Jacksonville Beach"),
}
_LETTER_SHA = {
    "wi-volta-2024040102.pdf": "4a6f6047d8c33ff6d90c66a23684eec6d24093c83581614aeb0ce5a3afd908e3",
    "fl-volta.pdf": "e7df4996c5a446226a2d68b601c0016fd1864be40d69f7de944b8ee5ec24a273",
}
_UNRECOVERED = {
    "a511cfe05bf198a8b05f03eca7a5a175a3361ed4": "WI",
    "08b04966d29efe1786992f3a67d592ff7b047b78": "CO",
    "a222637b6e8c56284ef7a5766bee5483d17b9d62": "FL",
    "77eae162b5f5caa9619111c3241762939eeb08a5": "FL",
}
_POSTAL_ZIP = re.compile(r"\b([A-Za-z]{2})\s+\d{5}(?:-\d{4})?\b")
_SECOND_STREET = re.compile(
    r"\b(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Blvd|Boulevard|Lane|Ln|"
    r"Parkway|Pkwy|Way|Court|Ct|Circle|Cir)\.?[,;]?\s+"
    r"\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z]", re.I)


def _ambiguous_nc_address(value: str) -> bool:
    """Hold concatenated sites and embedded foreign mailing addresses."""
    return bool(_SECOND_STREET.search(value)) or any(
        match[1].upper() in _STATE_NAMES and match[1].upper() != "NC"
        for match in _POSTAL_ZIP.finditer(value))


def verify(root: Path = DEFAULT_ROOT) -> dict[str, dict]:
    """Check every archived byte against its dated manifest before use."""
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format") != "warn-quality-evidence-v1":
        raise ValueError("unsupported quality evidence manifest")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len({e.get("path") for e in entries}) != len(entries):
        raise ValueError("missing or duplicate quality evidence files")
    listed = {}
    for entry in entries:
        name = entry.get("path")
        if (not isinstance(name, str) or Path(name).name != name
                or Path(name).suffix not in {".pdf", ".xlsx"}):
            raise ValueError(f"unsafe quality evidence path: {name}")
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"missing quality evidence: {name}")
        content = path.read_bytes()
        if (len(content) != entry.get("size") or
                hashlib.sha256(content).hexdigest() != entry.get("sha256")):
            raise ValueError(f"quality evidence hash mismatch: {name}")
        listed[name] = entry
    for name, digest in _LETTER_SHA.items():
        if name not in listed or listed[name]["sha256"] != digest:
            raise ValueError(f"unexpected official Volta letter: {name}")
    actual = {p.name for p in root.iterdir() if p.is_file() and p.name != "manifest.json"}
    if actual != set(listed):
        raise ValueError("unlisted quality evidence artifact")
    return listed


def _source(entry: dict, *, locator: str) -> dict:
    return {
        "url": entry["url"], "artifact": entry["path"],
        "sha256": entry["sha256"], "retrieved_on": entry["retrieved_on"],
        "document_type": entry["document_type"], "row": locator,
    }


def _current_rows(conn, states: tuple[str, ...]):
    marks = ",".join("?" for _ in states)
    return conn.execute(
        "SELECT n.*, v.fields_json FROM notices n JOIN notice_versions v "
        "ON v.notice_id=n.id AND v.version=n.current_version "
        f"WHERE n.state IN ({marks}) ORDER BY n.dedupe_key", states,
    ).fetchall()


def _raw(row) -> dict:
    fields = json.loads(row["fields_json"])
    raw = json.loads(fields.get("raw_extra") or "{}")
    return raw if isinstance(raw, dict) else {}


def _update(conn, row, quality: dict, observed_at: str, *,
            site_address: str | None = None, clear_notice_date: bool = False,
            clear_site_address: bool = False) -> bool:
    details = json.loads(row["source_details"] or "{}")
    if details.get("quality_evidence") == quality and (
        (site_address is None or row["site_address"] == site_address)
        and (not clear_site_address or row["site_address"] is None)
    ) and (not clear_notice_date or row["notice_date"] is None):
        return False
    details["quality_evidence"] = quality
    conn.execute(
        "UPDATE notices SET source_details=?, "
        "site_address=CASE WHEN ? THEN NULL ELSE COALESCE(?,site_address) END, "
        "notice_date=CASE WHEN ? THEN NULL ELSE notice_date END, "
        "notice_date_precision=CASE WHEN ? THEN NULL ELSE notice_date_precision END, "
        "notice_date_basis=CASE WHEN ? THEN NULL ELSE notice_date_basis END "
        "WHERE id=?",
        (json.dumps(details, sort_keys=True, ensure_ascii=False), int(clear_site_address),
         site_address,
         int(clear_notice_date), int(clear_notice_date), int(clear_notice_date), row["id"]),
    )
    return append_repair_version(conn, row["id"], observed_at)


def _letter_text(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _attach_volta(conn, root: Path, manifest: dict, observed_at: str,
                  states: set[str], require_targets: bool) -> dict:
    texts = {name: _letter_text(root / name) for name in _LETTER_SHA}
    for name, text in texts.items():
        if not all(token in text for token in (
            "March 29, 2024", "Volta Charging Industries, LLC", "May 31, 2024",
            "155 De Haro Street", "San Francisco", "Lake Geneva",
        )):
            raise ValueError(f"Volta letter text changed: {name}")
    for city in ("Cooper City", "Jacksonville Beach", "Miami"):
        if city not in texts["fl-volta.pdf"]:
            raise ValueError(f"Florida Volta letter lacks {city}")
    changed = 0
    found = set()
    for row in _current_rows(conn, tuple(sorted(states & {"WI", "FL"}))):
        key = row["dedupe_key"]
        if key not in _VOLTA:
            continue
        state, agency_location, affected_city = _VOLTA[key]
        found.add(key)
        if (row["state"] != state or row["employer_name"] != "Volta Charging Industries"
                or row["location"] != agency_location or
                row["notice_date"] not in ("2024-04-01", None)
                or row["effective_date"] != "2024-05-31"
                or row["employees_affected"] != 1):
            raise ValueError(f"Volta target row changed: {key}")
        name = "wi-volta-2024040102.pdf" if state == "WI" else "fl-volta.pdf"
        quality = {
            "rule": "volta_original_letter_roles_v1", "status": "original_letter_verified",
            "location_role": "employer_mailing" if state == "WI" else "remote_worker_location",
            "employer_name_verbatim": "Volta Charging Industries, LLC",
            "sites": [{"role": "remote_worker_location", "city": affected_city,
                       "state": state, "workers": 1,
                       "source_text": f"{affected_city} {state}"},
                      {"role": "employer_mailing", "address":
                       "155 De Haro Street San Francisco, CA 94103",
                       "city": "San Francisco", "state": "CA"}],
            "dates": [
                {"role": "letter_date", "date": "2024-03-29",
                 "source_field": "letter heading", "page": 1},
                {"role": "agency_received" if state == "WI" else "agency_notification",
                 "date": "2024-04-01", "source_field":
                 "Notice Received" if state == "WI" else "State Notification Date"},
            ],
            "sources": [_source(manifest[name], locator="page:1,5" if state == "WI"
                                else "page:1,4")],
        }
        changed += _update(conn, row, quality, observed_at, clear_notice_date=True)
    expected = {key for key, (state, _, _) in _VOLTA.items() if state in states}
    if require_targets and found != expected:
        raise ValueError(f"Volta source-backed targets missing: {sorted(expected-found)}")
    return {"matched": len(found), "changed": changed}


def _attach_ca(conn, root: Path, manifest: dict, observed_at: str) -> dict:
    records = collect_records(root)
    matcher = CaMatcher(records)
    resolver = Resolver()
    outcomes = Counter()
    tentative = []
    by_source = defaultdict(list)
    for row in _current_rows(conn, ("CA",)):
        raw = _raw(row)
        notice = dict(row)
        notice["county"] = raw.get("county") or raw.get("County")
        result = matcher.match(notice)
        outcomes[result.reason] += 1
        if result.status != "matched":
            continue
        record = result.record
        anchor = (record.source_file, record.source_sha256, record.locators)
        by_source[anchor].append(row["dedupe_key"])
        tentative.append((row, record, anchor))
    changed = 0
    for row, record, anchor in tentative:
        if len(by_source[anchor]) != 1:
            outcomes["multiple_notice_targets"] += 1
            continue
        if row["site_address"] and row["site_address"] != record.address:
            outcomes["existing_address_conflict"] += 1
            continue
        entry = manifest[record.source_file]
        if entry["sha256"] != record.source_sha256:
            raise ValueError(f"California source digest changed: {record.source_file}")
        resolved = resolver.resolve("CA", record.address)
        place = resolved.get("place_name")
        city = place.removesuffix(" city") if place else None
        quality = {
            "rule": "ca_edd_exact_report_row_v1", "status": "agency_report_row_matched",
            "employer_name_verbatim": record.company,
            "sites": [{"role": "affected_worksite", "address": record.address,
                       "city": city, "city_basis": "census_resolution_of_report_address" if city else None,
                       "state": "CA", "county": record.county,
                       "workers": record.workers, "source_field": "Address"}],
            "dates": ([{"role": "agency_received", "date": record.received_date,
                        "source_field": "Received Date"}] if record.received_date else [])
                     + ([{"role": "agency_processed", "date": record.processed_date,
                          "source_field": "Processed Date"}] if record.processed_date else []),
            "sources": [_source(entry, locator=locator)
                        for locator in record.locators],
        }
        changed += _update(conn, row, quality, observed_at,
                           site_address=record.address)
    return {"report_rows_with_address": len(records),
            "matched": outcomes["exact_source_row"], "changed": changed,
            "reasons": dict(sorted(outcomes.items()))}


def _hold_nc_site(conn, row, raw: dict, observed_at: str) -> bool:
    raw_text = json.loads(row["fields_json"]).get("raw_extra") or ""
    quality = {
        "rule": "labeled_agency_site_v1", "status": "site_address_ambiguous",
        "raw_site_text": raw.get("Address 1"), "raw_city_text": raw.get("City"),
        "sites": [], "dates": [],
        "sources": [{"url": row["source_url"], "artifact": "raw/nc.csv",
                     "source_row_sha256": hashlib.sha256(raw_text.encode()).hexdigest(),
                     "row": f"report:{(raw.get('Warn Number') or '').strip()}",
                     "document_type": "agency_report_row"}],
    }
    previous = json.loads(row["source_details"] or "{}").get("quality_evidence") or {}
    previous_addresses = {site.get("address") for site in previous.get("sites", [])
                          if isinstance(site, dict) and site.get("role") == "affected_worksite"}
    return _update(conn, row, quality, observed_at,
                   clear_site_address=row["site_address"] in previous_addresses)


def _attach_labeled_sites(conn, observed_at: str, states: set[str]) -> dict:
    """Surface explicit affected-location fields in NC and MD agency rows."""
    outcomes = Counter()
    changed = 0
    for row in _current_rows(conn, tuple(sorted(states & {"NC", "MD"}))):
        raw = _raw(row)
        state = row["state"]
        if state == "NC":
            address_text = raw.get("Address 1") or ""
            if _ambiguous_nc_address(address_text):
                changed += _hold_nc_site(conn, row, raw, observed_at)
                outcomes["ambiguous_labeled_site"] += 1
                continue
            address = clean_address(address_text, state)
            city = (raw.get("City") or "").strip() or None
            if not address or not raw.get("Number affected at this location"):
                outcomes["no_labeled_site"] += 1
                continue
            if city and not address.casefold().endswith(city.casefold()):
                address = f"{address} {city}"
            if not clean_address(address, state):
                changed += _hold_nc_site(conn, row, raw, observed_at)
                outcomes["ambiguous_labeled_site"] += 1
                continue
            county = (raw.get("County") or "").strip() or None
            received = (raw.get("Date Received by NC") or "").strip()
            dates = []
            if received:
                from warnlive.normalize.details import _date

                parsed = _date(received)
                if parsed:
                    dates.append({"role": "agency_received", "date": parsed,
                                  "source_field": "Date Received by NC"})
            locator = f"report:{(raw.get('Warn Number') or '').strip()}"
            source_field = "Address 1"
        else:
            address = clean_address(raw.get("Location"), state)
            if not address:
                outcomes["no_labeled_site"] += 1
                continue
            city = county = None
            dates = []
            locator = "raw_row_sha256"
            source_field = "Location"
        if row["site_address"] and row["site_address"] != address:
            outcomes["existing_address_conflict"] += 1
            continue
        raw_text = json.loads(row["fields_json"]).get("raw_extra") or ""
        raw_sha = hashlib.sha256(raw_text.encode()).hexdigest()
        quality = {
            "rule": "labeled_agency_site_v1", "status": "agency_labeled_site",
            "sites": [{"role": "affected_worksite", "address": address,
                       "city": city, "county": county, "state": state,
                       "source_field": source_field}],
            "dates": dates,
            "sources": [{"url": row["source_url"], "artifact": f"raw/{state.lower()}.csv",
                         "source_row_sha256": raw_sha, "row": locator,
                         "document_type": "agency_report_row"}],
        }
        changed += _update(conn, row, quality, observed_at, site_address=address)
        outcomes["matched"] += 1
    return {"matched": outcomes["matched"], "changed": changed,
            "reasons": dict(sorted(outcomes.items()))}


def _mark_unrecovered(conn, observed_at: str, states: set[str]) -> dict:
    """Record the downstream audit's search status, never a filing verdict."""
    found = set()
    changed = 0
    for row in _current_rows(conn, tuple(sorted(states & {"WI", "CO", "FL"}))):
        key = row["dedupe_key"]
        if key not in _UNRECOVERED:
            continue
        if row["state"] != _UNRECOVERED[key]:
            raise ValueError(f"unrecovered audit target changed state: {key}")
        found.add(key)
        quality = {
            "rule": "downstream_linkage_audit_2026_09_24",
            "status": "specific_source_not_recovered_in_audit",
            "audit_note": "No specific original letter or agency row was recovered in the 2026-09-24 sample audit; admission is unchanged.",
            "sites": [], "dates": [], "sources": [],
        }
        changed += _update(conn, row, quality, observed_at)
    return {"matched": len(found), "changed": changed}


def apply(conn, root: Path = DEFAULT_ROOT, observed_at: str = "2026-09-24",
          *, states: set[str] | None = None, require_volta: bool = True) -> dict:
    """Attach pinned evidence atomically; safe to call after each scrape."""
    manifest = verify(root)
    selected = states if states is not None else {"WI", "FL", "CO", "CA", "NC", "MD"}
    with conn:
        volta = (_attach_volta(conn, Path(root), manifest, observed_at,
                               selected, require_volta)
                 if selected & {"WI", "FL"} else None)
        ca = _attach_ca(conn, Path(root), manifest, observed_at) if "CA" in selected else None
        labeled_sites = (_attach_labeled_sites(conn, observed_at, selected)
                         if selected & {"NC", "MD"} else None)
        unrecovered = (_mark_unrecovered(conn, observed_at, selected)
                       if selected & {"WI", "CO", "FL"} else None)
    return {"manifest_sha256": hashlib.sha256((Path(root) / "manifest.json").read_bytes()).hexdigest(),
            "volta": volta, "ca": ca, "labeled_sites": labeled_sites,
            "unrecovered_audit_sources": unrecovered}
