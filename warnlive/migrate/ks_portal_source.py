"""Capture and audit the public KANSASWORKS WARN listing and detail pages.

The capture is deliberately slow and resumable. It keeps the original HTML;
parsing creates a review inventory, never canonical notices. A later admission
step must reconcile each stable portal record number against the database.
"""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from difflib import SequenceMatcher
import gzip
import hashlib
import io
import json
import re
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from warnlive.normalize import admission
from warnlive.normalize.admission import ks_event_signature

BASE = "https://www.kansasworks.com"
SEARCH = f"{BASE}/search/warn_lookups?commit=Search&q%5Bnotice_eq%5D=true"
DETAIL = re.compile(r"^/search/warn_lookups/(\d+)$")
HEADERS = ["Employer", "City", "ZIP", "LWIB Area", "Notice Date", "WARN Type"]
RAW_COLUMNS = [
    "employer", "notice_date", "number_of_employees_affected", "warn_type",
    "city", "zip", "lwib_area", "address", "record_number", "detail_page_url",
]


def parse_listing(html: bytes) -> tuple[list[dict], int]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", attrs={"role": "grid"})
    if table is None or "WARN entries" not in table.get_text(" ", strip=True):
        raise ValueError("Kansas WARN listing table missing")
    headers = [cell.get_text(" ", strip=True) for cell in table.select("thead th")]
    if headers != HEADERS:
        raise ValueError(f"Kansas WARN listing columns changed: {headers}")
    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) != len(HEADERS):
            raise ValueError("Kansas WARN listing row width changed")
        link = cells[0].find("a", href=True)
        match = DETAIL.fullmatch(link["href"] if link else "")
        if not match:
            raise ValueError("Kansas WARN listing row lacks record number")
        values = [cell.get_text(" ", strip=True) for cell in cells]
        if values[-1] != "WARN":
            raise ValueError(f"Kansas WARN filter returned other type: {values[-1]}")
        rows.append(dict(zip(
            ["employer", "city", "zip", "lwib_area", "notice_date", "warn_type"],
            values,
        )) | {"record_number": match.group(1), "detail_page_url": urljoin(BASE, link["href"])})
    if not rows:
        raise ValueError("Kansas WARN listing returned no rows")
    pages = [int(link["aria-label"].removeprefix("Page "))
             for link in soup.select('[aria-label="Pagination"] a[aria-label^="Page "]')
             if link["aria-label"].removeprefix("Page ").isdigit()]
    current = soup.select_one('[aria-label="Pagination"] [aria-current="page"]')
    if current and current.get_text(strip=True).isdigit():
        pages.append(int(current.get_text(strip=True)))
    return rows, max(pages, default=1)


def parse_detail(html: bytes) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    fields = {}
    for title in soup.select(".definition-list__title"):
        value = title.find_next_sibling(class_="definition-list__definition")
        if value is None:
            raise ValueError("Kansas WARN detail field lacks value")
        name = title.get_text(" ", strip=True)
        if name in fields:
            raise ValueError(f"Kansas WARN detail repeats field: {name}")
        fields[name] = value.get_text(" ", strip=True)
    if not fields.get("Company Name"):
        raise ValueError("Kansas WARN detail lacks company name")
    if "Notice Date" not in fields:
        raise ValueError("Kansas WARN detail lacks notice-date field")
    if "Number of Employees Affected" not in fields:
        raise ValueError("Kansas WARN detail lacks affected-worker field")
    return fields


def suspected_variant_pairs(rows: list[dict], details: list[dict]) -> dict[str, list[str]]:
    """Find same-day employer aliases that need source-level event review."""
    grouped: dict[str, list[tuple[dict, dict]]] = defaultdict(list)
    for row, detail in zip(rows, details):
        if detail["Notice Date"]:
            grouped[detail["Notice Date"]].append((row, detail))
    related: dict[str, set[str]] = defaultdict(set)
    for group in grouped.values():
        for index, (left, a) in enumerate(group):
            for right, b in group[index + 1:]:
                x = ks_event_signature(a["Company Name"], a["Notice Date"])[0]
                y = ks_event_signature(b["Company Name"], b["Notice Date"])[0]
                if x == y:
                    continue
                similarity = SequenceMatcher(None, x, y).ratio()
                short, long = sorted((x, y), key=len)
                same_count = (
                    a["Number of Employees Affected"]
                    == b["Number of Employees Affected"]
                    and a["Number of Employees Affected"] not in ("", "0")
                )
                plausible = (
                    similarity >= 0.85
                    or (same_count and (similarity >= 0.65 or len(short) >= 4 and short in long))
                    or (len(short) >= 8 and short in long and similarity >= 0.6)
                )
                if plausible:
                    aid, bid = left["record_number"], right["record_number"]
                    related[aid].add(bid)
                    related[bid].add(aid)
    return {key: sorted(value | {key}, key=int) for key, value in related.items()}


def _fetch(session: requests.Session, url: str, delay: float) -> bytes:
    for attempt in range(4):
        time.sleep(delay if attempt == 0 else max(30, delay * 2 ** attempt))
        response = session.get(url, timeout=30)
        if response.status_code in {429, 503} and attempt < 3:
            continue
        response.raise_for_status()
        if not response.content:
            raise ValueError(f"empty Kansas source page: {url}")
        return response.content
    raise AssertionError("unreachable retry state")


def _save(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def _load_or_fetch(session: requests.Session, path: Path, url: str, delay: float) -> bytes:
    if path.is_file():
        return path.read_bytes()
    content = _fetch(session, url, delay)
    _save(path, content)
    return content


def capture(out_dir: Path, *, phase: str = "all", delay: float = 2.0,
            max_new: int | None = None) -> dict:
    """Capture WARN-filtered listings and details, retaining partial progress."""
    if delay < 1:
        raise ValueError("Kansas capture delay must be at least one second")
    out_dir = Path(out_dir)
    session = requests.Session()
    session.headers["User-Agent"] = "WARN-register-source-audit/1.0 (slow public-page capture)"
    first_path = out_dir / "listings" / "page-001.html"
    first = _load_or_fetch(session, first_path, SEARCH, delay)
    _, page_count = parse_listing(first)
    if phase in {"all", "listings"}:
        for page in range(2, page_count + 1):
            path = out_dir / "listings" / f"page-{page:03d}.html"
            url = SEARCH + f"&page={page}"
            _load_or_fetch(session, path, url, delay)
            if page % 10 == 0 or page == page_count:
                print(f"Kansas listings: {page}/{page_count}", flush=True)

    rows = []
    for page in range(1, page_count + 1):
        path = out_dir / "listings" / f"page-{page:03d}.html"
        if not path.is_file():
            raise ValueError(f"Kansas listing capture incomplete: page {page}")
        page_rows, seen_last = parse_listing(path.read_bytes())
        if seen_last > page_count:
            raise ValueError("Kansas pagination grew during capture; restart listing phase")
        rows.extend(page_rows)
    ids = [row["record_number"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Kansas listing contains duplicate record numbers")

    if phase in {"all", "details"}:
        fetched = 0
        for row in sorted(rows, key=lambda item: int(item["record_number"])):
            path = out_dir / "details" / f"{int(row['record_number']):06d}.html"
            if path.is_file():
                continue
            if max_new is not None and fetched >= max_new:
                break
            content = _load_or_fetch(session, path, row["detail_page_url"], delay)
            parse_detail(content)
            fetched += 1
            if fetched % 50 == 0:
                print(f"Kansas details: {fetched} new, {len(rows)} listed", flush=True)

    for row in rows:
        path = out_dir / "details" / f"{int(row['record_number']):06d}.html"
        if path.is_file():
            row["detail"] = parse_detail(path.read_bytes())
    files = sorted((out_dir / "listings").glob("*.html"))
    files += sorted((out_dir / "details").glob("*.html"))
    manifest = {
        "source": SEARCH,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "listing_pages": page_count,
        "listed_warn_rows": len(rows),
        "detail_pages": sum("detail" in row for row in rows),
        "files": [
            {"path": str(path.relative_to(out_dir)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in files
        ],
    }
    _save(out_dir / "inventory.json", (json.dumps({"manifest": manifest, "rows": rows}, indent=2) + "\n").encode())
    return manifest


def stage_raw(out_dir: Path) -> dict[str, int]:
    """Verify a complete HTML capture and stage only internally consistent rows."""
    out_dir = Path(out_dir)
    payload = json.loads((out_dir / "inventory.json").read_text())
    manifest, rows = payload["manifest"], payload["rows"]
    if manifest["listed_warn_rows"] != len(rows) or manifest["detail_pages"] != len(rows):
        raise ValueError("Kansas detail capture is incomplete")
    expected_files = {item["path"]: item["sha256"] for item in manifest["files"]}
    actual_files = {
        str(path.relative_to(out_dir))
        for folder in ("listings", "details")
        for path in (out_dir / folder).glob("*.html")
    }
    if set(expected_files) != actual_files:
        raise ValueError("Kansas capture file set changed")
    for name, digest in expected_files.items():
        if hashlib.sha256((out_dir / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Kansas capture checksum changed: {name}")
    original_rows = []
    for page in range(1, manifest["listing_pages"] + 1):
        listed, _ = parse_listing(
            (out_dir / "listings" / f"page-{page:03d}.html").read_bytes()
        )
        original_rows.extend(listed)
    if len(original_rows) != len(rows):
        raise ValueError("Kansas listing inventory changed")

    details = []
    for original, row in zip(original_rows, rows):
        detail_path = out_dir / "details" / f"{int(original['record_number']):06d}.html"
        detail = parse_detail(detail_path.read_bytes())
        if original != {key: row[key] for key in original} or row.get("detail") != detail:
            raise ValueError(f"Kansas inventory changed: {original['record_number']}")
        details.append(detail)

    # A portal record number identifies a row, not necessarily an independent
    # filing. Same-employer/day rows may be separate sites or repetitions of
    # one filing, even when their workforce areas differ. Hold the entire
    # group until source evidence establishes event identity and allocation.
    by_apparent_event: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for row, detail in zip(original_rows, details):
        signature = ks_event_signature(detail["Company Name"], detail["Notice Date"])
        if signature:
            by_apparent_event[signature].append(row["record_number"])
    ambiguous = {
        record_number: sorted(group, key=int)
        for group in by_apparent_event.values() if len(group) > 1
        for record_number in group
    }
    variants = suspected_variant_pairs(original_rows, details)

    ready, held, warnings = [], [], []
    for original, detail in zip(original_rows, details):
        reason = None
        if original["employer"] != detail["Company Name"]:
            reason = "listing_detail_employer_disagreement"
        elif original["notice_date"] and original["notice_date"] != detail["Notice Date"]:
            reason = "listing_detail_notice_date_disagreement"
        elif not detail["Notice Date"]:
            reason = "missing_notice_date"
        elif original["record_number"] in ambiguous:
            reason = "same_employer_day_event_identity_unresolved"
        elif original["record_number"] in variants:
            reason = "same_day_employer_variant_event_identity_unresolved"
        if reason:
            held.append({"record_number": original["record_number"], "reason": reason,
                         "listing": original, "detail": detail,
                         "related_record_numbers": (
                             ambiguous.get(original["record_number"])
                             or variants.get(original["record_number"], [])
                         )})
            continue
        workers = detail["Number of Employees Affected"].replace(",", "").strip()
        if workers and not workers.isdecimal():
            warnings.append({
                "record_number": original["record_number"],
                "reason": "unparseable_worker_count",
                "source_text": detail["Number of Employees Affected"],
            })
            workers = ""
        ready.append({
            "employer": original["employer"], "notice_date": detail["Notice Date"],
            "number_of_employees_affected": workers,
            "warn_type": original["warn_type"], "city": original["city"],
            "zip": original["zip"], "lwib_area": original["lwib_area"],
            "address": detail.get("Address", ""),
            "record_number": original["record_number"],
            "detail_page_url": original["detail_page_url"],
        })
    path = out_dir / "ks.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, RAW_COLUMNS)
        writer.writeheader()
        writer.writerows(ready)
    (out_dir / "holds.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in held)
    )
    (out_dir / "field_warnings.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in warnings)
    )
    return {"listed": len(rows), "staged": len(ready), "held": len(held),
            "unparseable_worker_counts": len(warnings)}


def freeze_capture(out_dir: Path, archive_path: Path) -> dict:
    """Preserve every audited source page, disposition, and derived CSV together."""
    out_dir, archive_path = Path(out_dir), Path(archive_path)
    if archive_path.exists():
        raise FileExistsError(f"Kansas evidence archive already exists: {archive_path}")
    if archive_path.resolve().is_relative_to(out_dir.resolve()):
        raise ValueError("Kansas evidence archive must be outside capture directory")
    staged = stage_raw(out_dir)
    held = [json.loads(line) for line in (out_dir / "holds.jsonl").read_text().splitlines()]
    signatures = set()
    for item in held:
        for source in (item["listing"], item["detail"]):
            employer = source.get("employer") or source.get("Company Name")
            source_date = source.get("notice_date") or source.get("Notice Date")
            if not source_date:
                continue
            day = None
            for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%Y-%m-%d"):
                try:
                    day = datetime.strptime(source_date, fmt).date().isoformat()
                    break
                except ValueError:
                    continue
            if day is None:
                raise ValueError(f"unparseable held Kansas notice date: {source_date}")
            signature = ks_event_signature(employer, day)
            if signature:
                signatures.add(signature)
    policy = {
        "source": "kansasworks_warn_portal",
        "capture_inventory_sha256": hashlib.sha256((out_dir / "inventory.json").read_bytes()).hexdigest(),
        "parser_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "admission_rules_sha256": hashlib.sha256(Path(admission.__file__).read_bytes()).hexdigest(),
        "held_ids": sorted((f"KS:{item['record_number']}" for item in held), key=lambda x: int(x[3:])),
        "held_signatures": [list(item) for item in sorted(signatures)],
    }
    (out_dir / "hold_policy.json").write_text(json.dumps(policy, sort_keys=True, indent=2) + "\n")
    if staged["listed"] != staged["staged"] + staged["held"]:
        raise ValueError("Kansas source rows have incomplete dispositions")
    inventory = json.loads((out_dir / "inventory.json").read_text())
    detail_urls = {
        f"details/{int(row['record_number']):06d}.html": row["detail_page_url"]
        for row in inventory["rows"]
    }
    provenance = []
    for item in inventory["manifest"]["files"]:
        name = item["path"]
        page = int(Path(name).stem.removeprefix("page-")) if name.startswith("listings/") else None
        url = (SEARCH + (f"&page={page}" if page and page > 1 else "")) if page else detail_urls[name]
        provenance.append({
            "path": name, "requested_url": url,
            "local_written_at": datetime.fromtimestamp(
                (out_dir / name).stat().st_mtime, timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    (out_dir / "provenance.json").write_text(
        json.dumps(provenance, sort_keys=True, indent=2) + "\n"
    )
    files = [
        path for folder in ("listings", "details")
        for path in (out_dir / folder).glob("*.html")
    ] + [out_dir / name for name in (
        "inventory.json", "ks.csv", "holds.jsonl", "field_warnings.jsonl",
        "hold_policy.json", "provenance.json",
    )]
    files.sort(key=lambda path: path.relative_to(out_dir).as_posix())
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with archive_path.open("xb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", filename="", mtime=0
        ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            for path in files:
                content = path.read_bytes()
                info = tarfile.TarInfo(path.relative_to(out_dir).as_posix())
                info.size = len(content)
                info.mtime = 0
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))
    except BaseException:
        archive_path.unlink(missing_ok=True)
        raise
    return {**staged, "archive": str(archive_path),
            "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
            "files": len(files),
            "raw_sha256": hashlib.sha256((out_dir / "ks.csv").read_bytes()).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phase", choices=["all", "listings", "details"], default="all")
    parser.add_argument("--delay", type=float, default=2.0)
    parser.add_argument("--max-new", type=int)
    parser.add_argument("--stage-raw", action="store_true",
                        help="Verify a completed capture and write ks.csv plus holds.jsonl")
    parser.add_argument("--freeze", type=Path,
                        help="Verify and freeze a completed capture into a reproducible archive")
    args = parser.parse_args()
    if args.freeze:
        print(json.dumps(freeze_capture(args.out, args.freeze), sort_keys=True))
    elif args.stage_raw:
        print(json.dumps(stage_raw(args.out), sort_keys=True))
    else:
        manifest = capture(args.out, phase=args.phase, delay=args.delay,
                           max_new=args.max_new)
        print(json.dumps({key: value for key, value in manifest.items() if key != "files"},
                         sort_keys=True))
