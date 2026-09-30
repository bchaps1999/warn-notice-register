"""Massachusetts: official EOLWD WARN workbooks and weekly CSV from mass.gov.

Ported from upstream warn-scraper PR #787 (author riordan); the workbook and
weekly-CSV parsing follows the PR.

Inputs: the mass.gov WARN index page, the fiscal-year workbooks it links
(``/doc/fyNN-warn-report/download``) and the weekly CSV
(``/files/csv/.../WARN Report for the Week Ending ....csv``). Network access:
www.mass.gov, and web.archive.org only when the archived-copy fallback is
enabled.

Outputs: ``{data_dir}/ma.csv`` (six source columns, deduplicated),
``{data_dir}/ma.fetch_manifest.json`` (per-file origin, HTTP status, SHA-256,
and for an archived copy its capture timestamp), and the fetched files under
``{cache_dir}/ma/``.

Access: www.mass.gov is served by Akamai with Bot Manager. A block is an
HTTP 403 "Not allowed | Mass Gov" page carrying ``x-reference-error``. From a
residential connection (2026-09-29) plain ``requests``, ``curl`` and
``niquests`` over HTTP/1.1 or HTTP/2 all receive 200. Two things get 403
there: a browser User-Agent sent over a non-browser TLS stack, and niquests
requests upgraded to HTTP/3 (QUIC) through the site's ``alt-svc`` header. So
the client keeps its own default User-Agent and HTTP/3 is disabled. GitHub
Actions runners have received 403 on the first index request, which is sent
before any HTTP/3 upgrade; that points to runner-network reputation rather
than the client. The collector does not disguise itself as a browser.

Fallback: when ``WARNLIVE_MA_ARCHIVE_FALLBACK=1`` and mass.gov refuses a
file, the collector takes the newest Internet Archive capture of that exact
URL whose archived HTTP status was 200. The bytes must look like the
expected file type (never the block page). The manifest records
``origin: internet_archive``, the first capture of that content, the latest
capture confirming it (Wayback revisits share the capture digest), and the
Wayback digest. That digest covers the archived transfer encoding, so it
identifies a capture but cannot be recomputed from the decoded bytes. An
archived copy is only as fresh as its
capture. Without the fallback a block raises ``SourceBlocked``, and the
state must be collected from a network mass.gov accepts (see README).
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin

import niquests
from bs4 import BeautifulSoup
from openpyxl import load_workbook

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mass.gov"
INDEX_URL = (
    "https://www.mass.gov/info-details/worker-adjustment-and-retraining-"
    "notification-act-warn-layoff-and-closure-updates"
)
ARCHIVE_FALLBACK_ENV = "WARNLIVE_MA_ARCHIVE_FALLBACK"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{url}"
MANIFEST_NAME = "ma.fetch_manifest.json"

CANONICAL_HEADER = [
    "RECEIVED",
    "EMPLOYER",
    "CITY/TOWN",
    "REGION",
    "DATE(S) OF LAYOFFS",
    "# EMPLOYEES IMPACTED",
]

# Older fiscal-year workbooks (FY2022, FY2023) use one worksheet per region
# with this fixed column order; region comes from the worksheet name.
REGION_SHEET_ORDER = [
    "RECEIVED",
    "EMPLOYER",
    "CITY/TOWN",
    "DATE(S) OF LAYOFFS",
    "# EMPLOYEES IMPACTED",
]

XLSX_MAGIC = b"PK\x03\x04"
BLOCK_PAGE_MARKER = b"Not allowed | Mass Gov"


class SourceBlocked(RuntimeError):
    """mass.gov refused the request and no permitted fallback supplied it."""


def _new_session() -> niquests.Session:
    # HTTP/3 off: niquests upgrades to QUIC via alt-svc, and Akamai answers
    # those requests with 403 even where HTTP/2 from the same host passes.
    return niquests.Session(disable_http3=True)


def _archive_fallback_enabled() -> bool:
    return os.environ.get(ARCHIVE_FALLBACK_ENV, "").strip().lower() in {"1", "true", "yes"}


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    data_dir = Path(data_dir)
    manifest_path = data_dir / MANIFEST_NAME
    manifest_path.unlink(missing_ok=True)  # never leave a previous run's provenance
    cache = Cache(cache_dir)
    fetcher = _Fetcher(_new_session(), allow_archive=_archive_fallback_enabled())

    html = fetcher.get(INDEX_URL, "index").decode("utf-8", errors="replace")
    cache.write("ma/index.html", html)

    soup = BeautifulSoup(html, "html.parser")
    excel_urls, csv_urls = _find_source_links(soup)
    logger.debug("MA: %d workbook(s), %d CSV(s)", len(excel_urls), len(csv_urls))
    if not excel_urls:
        raise ValueError("MA index lists no fiscal-year workbooks")
    if not csv_urls:
        logger.warning("MA index lists no weekly CSV; notices since the last workbook update are absent")

    master_list: list = []
    for url in excel_urls:
        name = url.split("/doc/")[1].split("/")[0]
        excel_path = cache.write_binary(f"ma/{name}.xlsx", fetcher.get(url, "xlsx"))
        master_list.extend(_parse_workbook(excel_path))

    for url in csv_urls:
        name = os.path.basename(unquote(url))
        raw = fetcher.get(url, "csv")
        cache.write_binary(f"ma/{name}", raw)
        rows = list(csv.reader(raw.decode("utf-8", errors="replace").splitlines()))
        master_list.extend(_parse_flat_rows(rows))

    deduped = _dedupe(master_list)
    logger.debug("MA: %d rows, %d after dedupe", len(master_list), len(deduped))

    data_path = data_dir / "ma.csv"
    utils.write_dict_rows_to_csv(
        data_path, CANONICAL_HEADER, deduped, extrasaction="raise"
    )
    manifest = fetcher.manifest()
    manifest["raw_sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if manifest["archived_files"]:
        logger.warning(
            "MA: %d of %d source files are Internet Archive copies (oldest capture %s)",
            manifest["archived_files"], len(manifest["files"]),
            manifest["oldest_confirmed_capture"],
        )
    return data_path


def _valid_payload(content: bytes, kind: str) -> bool:
    """Reject block pages and HTML error bodies posing as source files."""
    if not content or BLOCK_PAGE_MARKER in content[:4000]:
        return False
    if kind == "xlsx":
        return content.startswith(XLSX_MAGIC)
    head = content[:512].lstrip().lower()
    if kind == "csv":
        return not head.startswith((b"<!doctype", b"<html"))
    return b"warn" in content.lower()  # index page


class _Fetcher:
    """Fetch each URL live, then (if allowed) from its newest good archive capture."""

    def __init__(self, session, allow_archive: bool, archive_session=None,
                 retries: int = 3, backoff: float = 5.0):
        self.session = session
        self.archive_session = archive_session or session
        self.allow_archive = allow_archive
        self.retries = retries
        self.backoff = backoff
        self.files: list[dict] = []

    def get(self, url: str, kind: str) -> bytes:
        live_status, live_error = None, None
        try:
            r = self.session.get(url, headers={"Referer": INDEX_URL}, timeout=120)
            live_status = r.status_code
            if r.status_code == 200 and _valid_payload(r.content, kind):
                self._record(url, r.content, origin="live", status=200)
                return r.content
            live_error = f"HTTP {r.status_code}" + (
                "" if r.status_code != 200 else " with an invalid payload"
            )
        except Exception as exc:  # noqa: BLE001 — connection errors fall through
            live_error = f"{type(exc).__name__}: {exc}"
        logger.warning("MA: live fetch of %s failed (%s)", url, live_error)
        if not self.allow_archive:
            raise SourceBlocked(
                f"mass.gov refused {url} ({live_error}); set {ARCHIVE_FALLBACK_ENV}=1 "
                "to use Internet Archive copies, or collect MA from a network "
                "mass.gov accepts"
            )
        content, capture = self._archived(url, kind)
        self._record(url, content, origin="internet_archive", status=live_status,
                     live_error=live_error, **capture)
        return content

    def _archived(self, url: str, kind: str) -> tuple[bytes, dict]:
        rows = self._cdx(url)
        good_digests = {row["digest"] for row in rows if row["statuscode"] == "200"}
        usable = [
            row for row in rows
            if row["statuscode"] == "200"
            or (row["statuscode"] == "-" and row["digest"] in good_digests)
        ]
        if not usable:
            raise SourceBlocked(f"mass.gov refused {url} and no 200 archive capture exists")
        latest = max(usable, key=lambda row: row["timestamp"])
        first_200 = min(
            (row for row in rows
             if row["statuscode"] == "200" and row["digest"] == latest["digest"]),
            key=lambda row: row["timestamp"],
        )
        capture_url = WAYBACK_RAW.format(timestamp=latest["timestamp"], url=url)
        r = self._retrying(capture_url, params=None)
        content = r.content
        if not _valid_payload(content, kind):
            raise SourceBlocked(f"archive copy of {url} at {latest['timestamp']} is not a {kind}")
        return content, {
            "capture_timestamp": first_200["timestamp"],
            "confirmed_through": latest["timestamp"],
            "capture_url": capture_url,
            "wayback_digest": latest["digest"],
        }

    def _cdx(self, url: str) -> list[dict]:
        r = self._retrying(CDX_URL, params={
            "url": url, "output": "json", "limit": "-50",
            "fl": "timestamp,statuscode,digest",
        })
        table = r.json() if r.content.strip() else []
        if not table:
            return []
        header, *body = table
        return [dict(zip(header, row)) for row in body]

    def _retrying(self, url: str, params):
        last = None
        for attempt in range(self.retries):
            try:
                r = self.archive_session.get(url, params=params, timeout=120)
                if r.status_code == 200:
                    return r
                last = f"HTTP {r.status_code}"
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < self.retries:
                time.sleep(self.backoff * (attempt + 1))
        raise SourceBlocked(f"Internet Archive request failed for {url}: {last}")

    def _record(self, url: str, content: bytes, origin: str, status, **extra) -> None:
        entry = {
            "source_url": url,
            "origin": origin,
            "live_status": status,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }
        entry.update({k: v for k, v in extra.items() if v is not None})
        self.files.append(entry)

    def manifest(self) -> dict:
        archived = [f for f in self.files if f["origin"] == "internet_archive"]
        return {
            "state": "MA",
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "archive_fallback_enabled": self.allow_archive,
            "archived_files": len(archived),
            "oldest_confirmed_capture": min(
                (f["confirmed_through"] for f in archived), default=None
            ),
            "files": self.files,
        }


def _find_source_links(soup: BeautifulSoup) -> tuple:
    excel_urls: set = set()
    csv_urls: set = set()
    for link in soup.find_all("a", href=True):
        href = link["href"]
        lower = href.lower()
        if "/doc/" in lower and "warn-report" in lower:
            excel_urls.add(urljoin(BASE_URL, href))
        elif lower.endswith(".csv") and "warn" in lower:
            csv_urls.add(urljoin(BASE_URL, href))
    return sorted(excel_urls), sorted(csv_urls)


def _parse_workbook(excel_path: Path) -> list:
    workbook = load_workbook(filename=excel_path, read_only=True, data_only=True)
    rows: list = []
    for sheet in workbook.worksheets:
        sheet_rows = [list(r) for r in sheet.iter_rows(values_only=True)]
        if _has_region_column(sheet_rows):
            rows.extend(_parse_flat_rows(sheet_rows))
        else:
            rows.extend(_parse_region_sheet(sheet_rows, sheet.title.strip()))
    workbook.close()
    return rows


def _has_region_column(rows: list) -> bool:
    header_index = _find_header_row(rows)
    if header_index is None:
        return False
    return any(_norm(cell) == "REGION" for cell in rows[header_index])


def _find_header_row(rows: list):
    for i, row in enumerate(rows):
        labels = {_norm(cell) for cell in row}
        if "EMPLOYER" in labels or "COMPANY NAME" in labels:
            return i
    return None


def _parse_flat_rows(rows: list) -> list:
    header_index = _find_header_row(rows)
    if header_index is None:
        return []
    positions: dict = {}
    for col, cell in enumerate(rows[header_index]):
        label = _norm(cell)
        if label in CANONICAL_HEADER and label not in positions:
            positions[label] = col
    parsed = []
    for row in rows[header_index + 1 :]:
        record = {
            field: _clean(row[pos]) if pos < len(row) else ""
            for field, pos in positions.items()
        }
        record = {field: record.get(field, "") for field in CANONICAL_HEADER}
        if _keep_row(record):
            parsed.append(record)
    return parsed


def _parse_region_sheet(rows: list, region: str) -> list:
    header_index = _find_header_row(rows)
    if header_index is None:
        return []
    parsed = []
    for row in rows[header_index + 1 :]:
        record = {field: "" for field in CANONICAL_HEADER}
        for pos, field in enumerate(REGION_SHEET_ORDER):
            if pos < len(row):
                record[field] = _clean(row[pos])
        record["REGION"] = region
        if _keep_row(record):
            parsed.append(record)
    return parsed


def _keep_row(record: dict) -> bool:
    employer = record.get("EMPLOYER", "")
    if not employer:
        return False
    if _norm(employer) in ("EMPLOYER", "COMPANY NAME"):
        return False
    if _norm(employer).startswith("TOTAL"):
        return False
    return True


def _dedupe(rows: list) -> list:
    seen = set()
    deduped = []
    for row in rows:
        key = tuple(row[field] for field in CANONICAL_HEADER)
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return f"{value.month}/{value.day}/{value.year}"
    return str(value).strip()


def _norm(value) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).upper()
