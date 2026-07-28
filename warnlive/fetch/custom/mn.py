"""Minnesota — custom adapter (not covered by warn-scraper).

MN DEED publishes monthly "Plant Closings/Mass Layoffs/WARN Report" PDFs
(plus annual roll-ups) listing every layoff Rapid Response tracked, with a
WARN Act yes/no column. The HTML pages are behind Radware bot protection,
but the PDF assets themselves are not — so we discover links from the live
pages if possible and fall back to the Wayback Machine's latest snapshot,
then fetch the PDFs directly.

The PDF tables have ruled headers but unruled data rows, with column sets
that differ between monthly and annual editions. We cluster the header words
into columns, then bucket characters below the header into those columns.
"""

from __future__ import annotations

import csv
import json
import logging
import re
import urllib.request
from datetime import date, datetime
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

from warn import utils
from warn.cache import Cache

logger = logging.getLogger(__name__)

PAGES = [
    "https://mn.gov/deed/business/layoff-resources/index.jsp",
    "https://mn.gov/deed/business/layoff-resources/warn-archive/",
]
WAYBACK_API = "https://archive.org/wayback/available?url={url}"
# A snapshot older than this cannot be listing the newest monthly PDFs;
# taking it silently is how a state goes stale while every run reads "ok".
WAYBACK_MAX_AGE_DAYS = 60
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

COLUMNS = [
    "RR Start Date",
    "Layoff Name",
    "City",
    "Industry",
    "Layoff Start",
    "WARN Act",
    "WARN Received",
    "Layoff Type",
    "Layoff Status",
    "TAA Related",
    "Affected Workers",
    "Layoff Count",
    "source_file",
]
LINE_TOLERANCE = 3


def scrape(
    data_dir: Path = utils.WARN_DATA_DIR,
    cache_dir: Path = utils.WARN_CACHE_DIR,
) -> Path:
    cache = Cache(cache_dir)
    pdf_urls: set[str] = set()
    for page_url in PAGES:
        html = _get_page_html(page_url, cache)
        if html:
            pdf_urls |= _find_report_links(html)
    if not pdf_urls:
        raise ValueError("MN: found no report PDF links (live or Wayback)")
    logger.debug("MN: %d report PDFs", len(pdf_urls))

    rows: list[dict] = []
    for url in sorted(pdf_urls):
        name = url.rsplit("/", 1)[-1]
        r = requests.get(url, headers=HEADERS, timeout=120)
        if not r.ok:
            logger.warning("MN: skipping %s (%s)", url, r.status_code)
            continue
        pdf_path = cache.write_binary(f"mn/{name}", r.content)
        parsed = parse_pdf(pdf_path)
        logger.debug("MN: %s -> %d rows", name, len(parsed))
        for row in parsed:
            row["source_file"] = name
        rows.extend(parsed)

    if not rows:
        raise ValueError("MN: parsed zero rows from report PDFs")

    data_path = data_dir / "mn.csv"
    with open(data_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return data_path


def _get_page_html(url: str, cache: Cache) -> str | None:
    """Fetch a mn.gov page, falling back to the Wayback Machine (the live
    pages sit behind a Radware JS challenge that blocks plain HTTP clients)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
        if r.ok and "perfdrive" not in r.url and "validate" not in r.url:
            return r.text
        logger.debug("MN: live fetch of %s blocked (%s)", url, r.url)
    except Exception as e:  # noqa: BLE001
        logger.debug("MN: live fetch of %s failed: %s", url, e)

    try:
        with urllib.request.urlopen(WAYBACK_API.format(url=url), timeout=60) as resp:
            snap = json.load(resp).get("archived_snapshots", {}).get("closest")
        if not snap:
            return None
        # The fallback is worth surfacing loudly: a stale snapshot omits
        # the newest monthly PDFs and the run still verdicts ok, so the
        # only trace that MN is running on old data is this log line.
        age_days = None
        ts = snap.get("timestamp") or ""
        if len(ts) >= 8:
            try:
                taken = datetime.strptime(ts[:8], "%Y%m%d").date()
                age_days = (date.today() - taken).days
            except ValueError:
                pass
        if age_days is not None and age_days > WAYBACK_MAX_AGE_DAYS:
            logger.warning(
                "MN: newest Wayback snapshot of %s is %d days old — refusing "
                "it; the freshest PDFs would be silently missing", url, age_days,
            )
            return None
        logger.warning(
            "MN: live page blocked; using Wayback snapshot %s (%s days old)",
            snap["url"], age_days if age_days is not None else "unknown",
        )
        r = requests.get(snap["url"], headers=HEADERS, timeout=120)
        r.raise_for_status()
        return r.text
    except Exception as e:  # noqa: BLE001
        logger.warning("MN: Wayback fallback for %s failed: %s", url, e)
        return None


def _find_report_links(html: str) -> set[str]:
    """Collect monthly/annual report PDF hrefs, normalizing Wayback prefixes."""
    soup = BeautifulSoup(html, "html5lib")
    urls: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" not in href.lower():
            continue
        name = href.rsplit("/", 1)[-1].lower()
        if not (
            "plant-closing" in name
            or "mass-layoff" in name
            or re.match(r"\d{4}-mass-layoff", name)
        ):
            continue
        # Strip a Wayback prefix like https://web.archive.org/web/2026.../https://mn.gov/...
        m = re.search(r"(https?://mn\.gov/.*)", href)
        if m:
            urls.add(m.group(1))
        elif href.startswith("/deed/"):
            urls.add("https://mn.gov" + href)
    return urls


def parse_pdf(path: Path) -> list[dict]:
    rows: list[dict] = []
    with pdfplumber.open(path) as pdf:
        columns: list[tuple[float, str]] | None = None
        for page in pdf.pages:
            words = page.extract_words()
            header = _find_header_columns(words)
            if header:
                columns = header["columns"]
                data_top = header["bottom"]
            elif columns is None:
                continue  # no header seen yet in this document
            else:
                data_top = 0  # continuation page: data starts at the top
            rows.extend(_parse_page(page, columns, data_top))
    return _merge_continuations(rows)


def _find_header_columns(words: list[dict]) -> dict | None:
    """Locate the header band and cluster its words into labeled columns."""
    anchor = next(
        (w for w in words if w["text"] == "Industry"), None
    )
    if anchor is None:
        return None
    # Two-line labels sit ~11pt apart; keep the band tight so group-header
    # lines ("RR Start Date: December 2024 (24 records)") don't pollute it.
    band = [w for w in words if -8 < w["top"] - anchor["top"] < 12]
    # Cluster by horizontal overlap (two-line labels stack vertically).
    clusters: list[dict] = []
    for w in sorted(band, key=lambda w: w["x0"]):
        target = next(
            (c for c in clusters if w["x0"] < c["x1"] + 6 and w["x1"] > c["x0"] - 6),
            None,
        )
        if target is None:
            clusters.append(
                {"x0": w["x0"], "x1": w["x1"], "words": [w]}
            )
        else:
            target["x0"] = min(target["x0"], w["x0"])
            target["x1"] = max(target["x1"], w["x1"])
            target["words"].append(w)

    columns: list[tuple[float, str]] = []
    for c in clusters:
        text = " ".join(
            w["text"] for w in sorted(c["words"], key=lambda w: (w["top"], w["x0"]))
        )
        label = re.sub(r"[*]+", "", text).strip()
        if label in COLUMNS:
            columns.append((c["x0"], label))
    if len(columns) < 8:
        return None
    bottom = max(w["bottom"] for c in clusters for w in c["words"])
    return {"columns": sorted(columns), "bottom": bottom}


def _parse_page(page, columns: list[tuple[float, str]], data_top: float) -> list[dict]:
    starts = [x for x, _ in columns]
    labels = [label for _, label in columns]

    lines: dict[float, list] = {}
    for ch in page.chars:
        if ch["top"] <= data_top:
            continue
        key = next((k for k in lines if abs(k - ch["top"]) <= LINE_TOLERANCE), None)
        lines.setdefault(key if key is not None else ch["top"], []).append(ch)

    rows = []
    for top in sorted(lines):
        cells = [""] * len(labels)
        last_x1 = [0.0] * len(labels)
        for ch in sorted(lines[top], key=lambda c: c["x0"]):
            idx = 0
            for i, start in enumerate(starts):
                if ch["x0"] >= start - 2:
                    idx = i
            if cells[idx] and ch["x0"] - last_x1[idx] > 1:
                cells[idx] += " "
            cells[idx] += ch["text"]
            last_x1[idx] = ch["x1"]
        row = dict(zip(labels, (c.strip() for c in cells)))
        rows.append(row)
    return rows


def _merge_continuations(rows: list[dict]) -> list[dict]:
    """Keep real data rows; fold wrapped name/city lines into their parent."""
    merged: list[dict] = []
    prev_was_real = False
    for row in rows:
        # A "(Grand) Totals (N records)" summary line can share a baseline
        # with the final data row; strip it out of the name cell.
        name = re.sub(
            r"\s*(Grand\s+)?Totals?\s*\(\d+\s+reco.*$",
            "",
            row.get("Layoff Name", ""),
        )
        row["Layoff Name"] = name
        status = row.get("Layoff Status", "")
        start = row.get("Layoff Start", "")
        if name.startswith("RR Start Date:") or name.lower().startswith("total"):
            prev_was_real = False
            continue
        # Real rows have a date-ish start or a numeric worker count; the
        # footnote paragraphs at the bottom of each report have neither.
        is_real = bool(
            re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", start)
            or re.match(r"^[\d,]+$", row.get("Affected Workers", ""))
        )
        if name and (status or start) and is_real:
            merged.append(row)
            prev_was_real = True
        elif prev_was_real and name and not any(
            row.get(k) for k in ("Layoff Start", "WARN Act", "Layoff Status", "Affected Workers")
        ):
            # A wrapped employer/city/industry cell continues its parent on
            # the very next line; anything after a gap is footnote text.
            prev = merged[-1]
            for key in ("Layoff Name", "City", "Industry", "Layoff Type"):
                if row.get(key):
                    prev[key] = f"{prev.get(key, '')} {row[key]}".strip()
            prev_was_real = False
        else:
            prev_was_real = False
    return merged
