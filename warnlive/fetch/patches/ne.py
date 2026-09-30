"""Nebraska — patched from upstream warn-scraper ne.py.

Upstream reads NDOL's current WARN page and, for 2010-2019, appends two
year reports into one CSV: WARNReportData (the WARN report) and
LayoffAndClosureReportData (NDOL's general layoff/closure report, many of
whose rows are not WARN filings). Its CSV cannot tell them apart.

This collector reads the same pages the same way (same cells, same order)
and adds report provenance to every row: ``source_report`` (warn_report or
layoff_closure_report), the page URL and row, the WARN page's notice
document link, and, for a layoff/closure row that repeats a WARN report row
(same employer and date), that WARN row. The normalizer holds
layoff/closure rows (normalize.custom.ne).

Network: the WARN page is fetched on every run and kept as a dated capture
(``archives/ne/warn-page-YYYYMMDD.html``); the 2010-2019 year reports are
cached (``archives/ne/{report}-{year}.html``). Every capture has a
url/retrieved_at/sha256 sidecar. Writes ``{data_dir}/ne.csv``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from warn import utils

from warnlive.backfill import state_archives as archives

logger = logging.getLogger(__name__)

PAGE_URL = archives.NE_DOL_ORIGINAL


def _fetch_page(cache_dir: Path) -> tuple[bytes, dict]:
    """Fetch the current WARN page and keep a dated, hashed copy."""
    req = urllib.request.Request(PAGE_URL, headers={"User-Agent": "warn-live collector"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        content = resp.read()
    if b"WARN Notices received by the State of" not in content:
        raise ValueError("NE: WARN page is not the expected NDOL page")
    now = datetime.now(timezone.utc)
    dest = Path(cache_dir) / "archives" / "ne" / f"warn-page-{now:%Y%m%d}.html"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    meta = {"url": PAGE_URL, "sha256": hashlib.sha256(content).hexdigest(),
            "retrieved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}
    dest.with_name(dest.name + ".json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    return content, meta


def page_rows(html: str) -> list[dict]:
    """The WARN page's rows in the collector's raw columns."""
    rows = archives.parse_ne_dol_page(html, PAGE_URL)
    short = [row for row in rows if len(row["cells"]) != 4]
    if short:
        raise ValueError(f"NE: {len(short)} WARN page rows lack the four columns")
    return [archives.ne_page_raw_row(row, PAGE_URL) for row in rows]


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    content, _meta = _fetch_page(Path(cache_dir))
    rows = page_rows(content.decode("utf-8", "replace"))
    report_rows, counts = archives.ne_report_rows(Path(cache_dir))
    logger.info("NE: %d WARN page rows; year reports %s", len(rows), counts)
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    data_path = Path(data_dir) / "ne.csv"
    with open(data_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=archives.NE_RAW_FIELDS)
        writer.writeheader()
        writer.writerows(rows + report_rows)
    return data_path
