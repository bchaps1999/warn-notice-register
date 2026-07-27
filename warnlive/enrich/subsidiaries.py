"""Subsidiary-to-parent mapping from SEC Exhibit 21 filings.

The largest employers still missing an identity are operating subsidiaries
of companies that do file with the SEC: Cessna is Textron, Dominick's was
Safeway, ABM Aviation is ABM Industries. Their names will never appear in
EDGAR's registrant index, because subsidiaries do not register — but every
10-K carries Exhibit 21, "Subsidiaries of the Registrant", which lists
them by legal name.

Crawling those exhibits gives a subsidiary-name -> parent-CIK index built
entirely from primary filings. It is a slow crawl (three requests per
registrant), so it writes progress as it goes and resumes where it left
off; the exhibits themselves are parsed and discarded rather than cached.

A WARN notice says "Cessna" where Exhibit 21 says "Cessna Aircraft
Company", so lookup allows a name to stand for the longer legal names it
begins — but only when every one of them belongs to the same parent.
Where two parents claim a name, the notice keeps no parent at all: a
subsidiary that changed hands cannot be assigned from the name alone.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from pathlib import Path
from threading import Lock
from time import monotonic, sleep

logger = logging.getLogger("warnlive")

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/"
PATH = Path("data/reference/subsidiaries.csv.gz")
OVERRIDES_PATH = Path("data/reference/subsidiary_overrides.csv")
PROGRESS_PATH = Path("workdir/backfill/cache/subsidiaries_progress.csv")
FIELDS = ["normalized_name", "parent_cik", "parent_name", "source_year"]

ANNUAL_FORMS = ("10-K", "10-K405", "10-KSB")
_EX21 = re.compile(r"ex.{0,3}-?_?21", re.IGNORECASE)
# Column headers and legends that sit in the same tables as the names.
_NOT_A_NAME = re.compile(
    r"^(name|subsidiar|jurisdiction|state|country|place|percent|ownership|"
    r"organi|incorporat|entity|list of|schedule|as of|exhibit|ex[-_ ]?21|"
    r"the registrant|total|\d)",
    re.IGNORECASE,
)


# Registrants are crawled concurrently, so pacing has to be global rather
# than a sleep per request: this admits one request every MIN_INTERVAL
# seconds across all workers, keeping the whole crawl under SEC's
# fair-access ceiling of 10 requests a second no matter the worker count.
WORKERS = 4
MIN_INTERVAL = 0.15
_throttle = Lock()
_last_request = 0.0


def _get(url: str) -> bytes | None:
    """Fetch via curl; SEC's WAF rejects Python HTTP clients outright."""
    global _last_request
    from warnlive.enrich.edgar import _user_agent

    with _throttle:
        wait = _last_request + MIN_INTERVAL - monotonic()
        if wait > 0:
            sleep(wait)
        _last_request = monotonic()
    proc = subprocess.run(
        ["curl", "-sf", "-m", "60", "-A", _user_agent(), url],
        capture_output=True,
    )
    return proc.stdout if proc.returncode == 0 else None


def _latest_annual(cik: int) -> tuple[str, str, str] | None:
    """(accession without dashes, filing year, registrant name) of the
    newest annual report."""
    raw = _get(SUBMISSIONS_URL.format(cik=cik))
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
        recent = payload["filings"]["recent"]
        entity = payload.get("name") or ""
    except (ValueError, KeyError):
        return None
    for form, acc, date in zip(
        recent.get("form", []), recent.get("accessionNumber", []),
        recent.get("filingDate", []),
    ):
        if form in ANNUAL_FORMS:
            return acc.replace("-", ""), date[:4], entity
    return None


def _exhibit_names(cik: int, acc: str) -> list[str]:
    """Company names listed in a filing's Exhibit 21, if it has one."""
    from bs4 import BeautifulSoup

    base = ARCHIVE_URL.format(cik=cik, acc=acc)
    listing = _get(base + "index.json")
    if listing is None:
        return []
    try:
        items = json.loads(listing)["directory"]["item"]
    except (ValueError, KeyError):
        return []
    doc = next((i["name"] for i in items if _EX21.search(i["name"])), None)
    if doc is None:
        return []
    body = _get(base + doc)
    if body is None:
        return []

    soup = BeautifulSoup(body.decode("utf-8", "replace"), "html.parser")
    names = []
    rows = soup.find_all("tr")
    if rows:
        # Tabular exhibits: the name is the first non-empty cell of a row.
        for tr in rows:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                names.append(cells[0])
    else:
        names = [line.strip() for line in soup.get_text("\n").splitlines()]
    return [n for n in names if _plausible(n)]


def _plausible(name: str) -> bool:
    return (
        4 <= len(name) <= 90
        and any(ch.isalpha() for ch in name)
        and ".htm" not in name.lower()
        and not _NOT_A_NAME.match(name)
    )


def refresh(out_path: Path = PATH, progress_path: Path = PROGRESS_PATH,
            limit: int | None = None) -> int:
    """Crawl Exhibit 21 for listed and WARN-relevant registrants.

    Resumable: every registrant's result is appended to the progress file
    as it is fetched, so an interrupted crawl continues rather than
    restarting. Re-run until it reports no remaining targets.
    """
    from warnlive.enrich.edgar import REFERENCE_PATH, SIC_PATH
    from warnlive.normalize.engine import normalized_employer

    targets: list[int] = []
    seen: set[int] = set()
    # Registrants our own notices already match come first: their corporate
    # families are the ones WARN filings actually name, so an interrupted
    # crawl still yields the subsidiaries most likely to be looked up.
    if SIC_PATH.exists():
        with gzip.open(SIC_PATH, "rt") as fh:
            for row in csv.DictReader(fh):
                cik = int(row["cik"])
                if cik not in seen:
                    seen.add(cik)
                    targets.append(cik)
    with gzip.open(REFERENCE_PATH, "rt") as fh:
        for row in csv.DictReader(fh):
            cik = int(row["cik"])
            if row["ticker"] and cik not in seen:
                seen.add(cik)
                targets.append(cik)

    done: set[int] = set()
    rows: list[tuple] = []
    if progress_path.exists():
        with open(progress_path, newline="") as fh:
            for row in csv.reader(fh):
                if not row:
                    continue
                if row[0]:  # a registrant-completed marker
                    done.add(int(row[0]))
                elif len(row) == 5:
                    rows.append(tuple(row[1:]))
    remaining = [c for c in targets if c not in done]
    if limit:
        remaining = remaining[:limit]
    logger.info(
        "subsidiaries: %d registrants to crawl (%d already done)",
        len(remaining), len(done),
    )

    def crawl(cik: int) -> tuple[int, list[tuple]]:
        found = _latest_annual(cik)
        if not found:
            return cik, []
        acc, year, parent = found
        out = []
        for name in _exhibit_names(cik, acc):
            norm = normalized_employer(name)
            if not norm or " " not in norm:
                continue  # one-word subsidiary names are too ambiguous
            out.append((norm, str(cik), parent, year))
        return cik, out

    progress_path.parent.mkdir(parents=True, exist_ok=True)
    with open(progress_path, "a", newline="") as fh:
        writer = csv.writer(fh)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = [pool.submit(crawl, cik) for cik in remaining]
            for i, future in enumerate(as_completed(futures), 1):
                try:
                    cik, found_rows = future.result()
                except Exception as exc:  # noqa: BLE001 — one bad filing
                    logger.warning("subsidiaries: crawl failed (%s)", exc)
                    continue
                rows.extend(found_rows)
                for row in found_rows:
                    writer.writerow(("", *row))
                writer.writerow((cik, "", "", "", ""))  # registrant done
                fh.flush()
                if i % 200 == 0:
                    logger.info(
                        "subsidiaries: %d/%d crawled, %d names",
                        i, len(remaining), len(rows),
                    )

    # One parent per name; a name two registrants both claim is dropped.
    by_name: dict[str, set[str]] = defaultdict(set)
    detail: dict[str, tuple] = {}
    for norm, cik, parent, year in rows:
        by_name[norm].add(cik)
        detail[norm] = (norm, cik, parent, year)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(FIELDS)
        for norm in sorted(by_name):
            if len(by_name[norm]) == 1:
                writer.writerow(detail[norm])
    kept = sum(1 for v in by_name.values() if len(v) == 1)
    logger.info(
        "subsidiaries: %d names -> one parent, %d contested -> %s",
        kept, len(by_name) - kept, out_path,
    )
    return kept


class Index:
    """Subsidiary-name lookup that tolerates a notice's shorter name."""

    def __init__(self, path: Path = PATH, overrides_path: Path = OVERRIDES_PATH):
        self.by_name: dict[str, dict] = {}
        self.by_first: dict[str, list[str]] = defaultdict(list)
        if path.exists():
            with gzip.open(path, "rt") as fh:
                for row in csv.DictReader(fh):
                    self.by_name[row["normalized_name"]] = row
        # Adjudicated links live in their own file and are applied last, so
        # they outrank the generated table and survive its regeneration.
        # Exhibit 21 lists a parent's subsidiaries under their registered
        # names; a state files "First Transit" or "Crothall Healthcare",
        # which belong to somebody but appear in no filing under that name.
        if overrides_path.exists():
            with open(overrides_path, newline="") as fh:
                for row in csv.DictReader(fh):
                    if row.get("normalized_name") and row.get("parent_cik"):
                        self.by_name[row["normalized_name"]] = row
        for name in self.by_name:
            self.by_first[name.split(" ", 1)[0]].append(name)

    def parent(self, norm: str | None) -> dict | None:
        """The parent of an employer name, or None if it has none or the
        name spans subsidiaries of different parents."""
        if not norm or len(norm) < 6:
            return None
        hit = self.by_name.get(norm)
        if hit:
            return hit
        # "Cessna" stands for CESSNA AIRCRAFT COMPANY and CESSNA FINANCE
        # CORPORATION alike, because both are Textron's.
        longer = [
            self.by_name[n]
            for n in self.by_first.get(norm.split(" ", 1)[0], ())
            if n.startswith(norm + " ")
        ]
        if longer and len({r["parent_cik"] for r in longer}) == 1:
            return longer[0]
        return None
