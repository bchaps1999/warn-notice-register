"""SEC EDGAR enrichment: match notices to CIKs via era-appropriate names.

EDGAR's quarterly full indexes (company.idx, 1993Q1-present) list every
filer with the company name AS OF that quarter, so a 2003 notice matches
the company's 2003 name even if it renamed since. `edgar-refresh` distills
those ~130 index files into data/reference/edgar_names.csv.gz — one row per
(normalized name, CIK) with the first/last year the name appeared — plus
tickers from the current company_tickers.json snapshot. Export-time
matching reads only the distilled file.

Matching policy (recorded per row in cik_match):
  exact       — normalized names equal, notice year within the name's
                active range +/- YEAR_TOLERANCE
  fuzzy:0.9x  — Jaro-Winkler >= FUZZY_MIN on normalized names, same first
                token, name >= MIN_FUZZY_LEN chars, token-compatible (see
                below), and exactly one CIK clears the bar; skipped
                entirely when an exact match exists

Token compatibility: whole-name JW alone accepts single-token swaps
("hacker group" ~ "baker group", "international paper" ~ "international
baler") and entity-qualifier tails ("wells fargo" ~ "wells fargo qp" — a
fund, not the bank). So each candidate token must fuzzily pair with a
notice token (>= TOKEN_MIN); the EDGAR side may have NO unpaired tokens,
the notice side at most one (WARN filers append site info; EDGAR names
with extra tokens are usually different legal entities).
Ambiguity (two CIKs for one name/era, or two fuzzy candidates) matches
nothing: a missing CIK is recoverable, a wrong one poisons every join.
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from time import sleep

import jellyfish

from warnlive.normalize.engine import normalized_employer

logger = logging.getLogger("warnlive")

INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/company.idx"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# SEC fair-access policy requires a declared two-part UA ("Name email") AND
# an Accept-Encoding header — without either its WAF serves 403 "Undeclared
# Automated Tool". Noreply-style multi-label email domains are rejected, so
# the UA comes from the environment rather than being committed here. Only
# edgar-refresh contacts the SEC; export-time matching reads the committed
# reference file and never needs this.
UA_ENV = "SEC_EDGAR_UA"
FIRST_YEAR = 1993

REFERENCE_PATH = Path("data/reference/edgar_names.csv.gz")
SIC_PATH = Path("data/reference/edgar_sic.csv.gz")
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

FUZZY_MIN = 0.95
TOKEN_MIN = 0.90  # typos score ~0.91 ("helthcare"), word swaps <=0.87 ("hacker"/"baker")
MIN_FUZZY_LEN = 8
YEAR_TOLERANCE = 2


def _user_agent() -> str:
    import os

    ua = os.environ.get(UA_ENV, "").strip()
    if not ua or "@" not in ua:
        raise RuntimeError(
            f"Set {UA_ENV} to a declared SEC user agent, e.g. "
            f'{UA_ENV}="Your Name you@example.com" — SEC fair-access policy '
            "requires a real contact and rejects undeclared clients."
        )
    return ua


def _get(url: str) -> bytes:
    """Fetch via curl: SEC's WAF 403s Python HTTP clients (urllib and
    niquests alike, regardless of headers) but passes curl's fingerprint."""
    import subprocess

    proc = subprocess.run(
        ["curl", "-sf", "-m", "120",
         "-A", _user_agent(), "-H", "Accept-Encoding: gzip, deflate", url],
        capture_output=True, check=True,
    )
    data = proc.stdout
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def refresh(cache_dir: Path, out_path: Path = REFERENCE_PATH, last_year: int = 2026) -> int:
    """Build the distilled (name, cik, years, ticker) reference file."""
    cache_dir = Path(cache_dir) / "edgar"
    cache_dir.mkdir(parents=True, exist_ok=True)

    # name -> cik -> [first_year, last_year]
    seen: dict[str, dict[int, list[int]]] = defaultdict(dict)
    for year in range(FIRST_YEAR, last_year + 1):
        for q in (1, 2, 3, 4):
            dest = cache_dir / f"company_{year}_q{q}.idx"
            if not dest.exists():
                try:
                    sleep(0.3)
                    dest.write_bytes(_get(INDEX_URL.format(year=year, q=q)))
                except Exception as exc:  # noqa: BLE001 — future quarters 404
                    logger.debug("EDGAR %s Q%s unavailable (%s)", year, q, exc)
                    continue
            for name, cik in _parse_idx(dest):
                norm = normalized_employer(name)
                if not norm:
                    continue
                span = seen[norm].setdefault(cik, [year, year])
                span[0], span[1] = min(span[0], year), max(span[1], year)
        logger.info("EDGAR %s indexed (%d names so far)", year, len(seen))

    tickers: dict[int, str] = {}
    try:
        for entry in json.loads(_get(TICKERS_URL)).values():
            tickers.setdefault(int(entry["cik_str"]), entry["ticker"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("EDGAR tickers snapshot unavailable (%s)", exc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["normalized_name", "cik", "first_year", "last_year", "ticker"])
        for norm in sorted(seen):
            for cik, (y0, y1) in sorted(seen[norm].items()):
                writer.writerow([norm, cik, y0, y1, tickers.get(cik, "")])
                n += 1
    logger.info("EDGAR reference written: %d (name, cik) rows -> %s", n, out_path)
    return n


_IDX_ROW = re.compile(r"^(.*?)\s{2,}[A-Za-z0-9/\-]+\s+(\d{1,10})\s+\d{4}-\d{2}-\d{2}\s+\S+\s*$")


def _parse_idx(path: Path):
    """Yield (company name, cik) from a company.idx file."""
    try:
        text = path.read_bytes().decode("latin-1")
    except OSError:
        return
    for line in text.splitlines():
        m = _IDX_ROW.match(line)
        if m and m.group(1).strip() and not m.group(1).startswith("Company Name"):
            yield m.group(1).strip(), int(m.group(2))


def _tokens_compatible(notice: str, candidate: str) -> bool:
    """Every candidate token must pair with a distinct notice token at
    >= TOKEN_MIN; at most one notice token may go unpaired."""
    remaining = candidate.split()
    unpaired = 0
    for token in notice.split():
        best_i, best = -1, 0.0
        for i, other in enumerate(remaining):
            s = jellyfish.jaro_winkler_similarity(token, other)
            if s > best:
                best, best_i = s, i
        if best >= TOKEN_MIN:
            remaining.pop(best_i)
        else:
            unpaired += 1
    return not remaining and unpaired <= 1


def load_sic(path: Path = SIC_PATH) -> dict[int, tuple[str, str]]:
    """cik -> (sic, sic_description); {} when the reference is absent."""
    if not path.exists():
        return {}
    out: dict[int, tuple[str, str]] = {}
    with gzip.open(path, "rt") as fh:
        for row in csv.DictReader(fh):
            out[int(row["cik"])] = (row["sic"], row["sic_description"])
    return out


def sic_refresh(conn, out_path: Path = SIC_PATH) -> int:
    """Fetch SIC codes for every CIK our notices match, incrementally.

    One submissions-API request per CIK not already in the reference;
    already-known CIKs are never refetched (SIC codes are near-immutable).
    """
    matcher = Matcher()
    known = load_sic(out_path)

    ciks: set[int] = set()
    for r in conn.execute(
        "SELECT DISTINCT employer_name, "
        "substr(COALESCE(notice_date, effective_date), 1, 4) AS y FROM notices"
    ):
        hit = matcher.match(r["employer_name"], int(r["y"]) if r["y"] else None)
        if hit:
            ciks.add(hit[0])
    missing = sorted(ciks - set(known))
    logger.info("EDGAR SIC: %d matched CIKs, %d to fetch", len(ciks), len(missing))

    for i, cik in enumerate(missing, 1):
        sleep(0.15)
        try:
            sub = json.loads(_get(SUBMISSIONS_URL.format(cik=cik)))
        except Exception as exc:  # noqa: BLE001 — dead CIKs 404
            logger.debug("EDGAR SIC: CIK %d unavailable (%s)", cik, exc)
            continue
        known[cik] = (str(sub.get("sic") or ""), str(sub.get("sicDescription") or ""))
        if i % 250 == 0:
            logger.info("EDGAR SIC: fetched %d/%d", i, len(missing))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["cik", "sic", "sic_description"])
        for cik in sorted(known):
            writer.writerow([cik, known[cik][0], known[cik][1]])
    logger.info("EDGAR SIC reference: %d CIKs -> %s", len(known), out_path)
    return len(known)


class Matcher:
    """In-memory era-aware name -> CIK matcher over the reference file."""

    def __init__(self, path: Path = REFERENCE_PATH):
        self.by_name: dict[str, list[tuple[int, int, int, str]]] = defaultdict(list)
        self.by_first_token: dict[str, set[str]] = defaultdict(set)
        with gzip.open(path, "rt") as fh:
            for row in csv.DictReader(fh):
                self.by_name[row["normalized_name"]].append(
                    (int(row["cik"]), int(row["first_year"]),
                     int(row["last_year"]), row["ticker"])
                )
        for name in self.by_name:
            self.by_first_token[name.split(" ", 1)[0]].add(name)
        self._cache: dict[tuple[str, int | None], tuple[int, str, str] | None] = {}

    def _era_ciks(self, name: str, year: int | None):
        out = []
        for cik, y0, y1, ticker in self.by_name.get(name, ()):
            if year is None or (y0 - YEAR_TOLERANCE <= year <= y1 + YEAR_TOLERANCE):
                out.append((cik, ticker))
        # Pre-1993 notices can only match 1993-era names
        if not out and year is not None and year < FIRST_YEAR:
            out = [(c, t) for c, y0, _, t in self.by_name.get(name, ()) if y0 == FIRST_YEAR]
        return out

    def match(self, employer_name: str | None, year: int | None):
        """Return (cik, ticker, method) or None."""
        norm = normalized_employer(employer_name)
        if not norm:
            return None
        key = (norm, year)
        if key in self._cache:
            return self._cache[key]
        result = None

        exact = self._era_ciks(norm, year)
        if len({c for c, _ in exact}) == 1:
            cik, ticker = exact[0]
            result = (cik, ticker, "exact")
        elif not exact and len(norm) >= MIN_FUZZY_LEN:
            first = norm.split(" ", 1)[0]
            candidates: dict[int, tuple[float, str]] = {}
            for cand in self.by_first_token.get(first, ()):
                jw = jellyfish.jaro_winkler_similarity(norm, cand)
                if jw < FUZZY_MIN or not _tokens_compatible(norm, cand):
                    continue
                for cik, ticker in self._era_ciks(cand, year):
                    prev = candidates.get(cik)
                    if prev is None or jw > prev[0]:
                        candidates[cik] = (jw, ticker)
            if len(candidates) == 1:
                (cik, (jw, ticker)), = candidates.items()
                result = (cik, ticker, f"fuzzy:{jw:.2f}")

        self._cache[key] = result
        return result
