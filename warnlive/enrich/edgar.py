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

from warnlive.normalize.engine import base_employer, normalized_employer

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
# Similar enough to be worth a second look, not enough to match on: these
# are collected as candidates for later adjudication, never auto-applied.
FUZZY_REVIEW_MIN = 0.90
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


# EDGAR appends the state/country of incorporation to a registrant name
# ("BANK OF AMERICA CORP /DE/", "WELLS FARGO & COMPANY/MN", "WALT DISNEY
# CO/CA/TA/"). It is filing metadata, not part of the name, and leaving it
# in blocks the exact match a notice for "Bank of America" should get.
_EDGAR_SUFFIX = re.compile(r"\s*/[A-Za-z]{0,4}/?\s*$")


def _edgar_name(raw: str) -> str:
    """A registrant name with its incorporation markers stripped."""
    name = raw.strip()
    for _ in range(4):  # several can be chained
        stripped = _EDGAR_SUFFIX.sub("", name)
        if stripped == name:
            break
        name = stripped
    return name or raw.strip()


def refresh(
    cache_dir: Path,
    out_path: Path = REFERENCE_PATH,
    last_year: int | None = None,
    keep_cache: bool = False,
) -> int:
    """Build the distilled (name, cik, years, ticker) reference file.

    The quarterly indexes total a few GB, so by default each is parsed and
    deleted rather than cached; pass keep_cache to retain them.
    """
    cache_dir = Path(cache_dir) / "edgar"
    cache_dir.mkdir(parents=True, exist_ok=True)

    from datetime import date as _date

    today = _date.today()
    current_quarter = (today.year, (today.month - 1) // 3 + 1)
    if last_year is None:
        last_year = today.year

    # name -> cik -> [first_year, last_year]
    seen: dict[str, dict[int, list[int]]] = defaultdict(dict)
    failed_quarters: list[str] = []
    for year in range(FIRST_YEAR, last_year + 1):
        for q in (1, 2, 3, 4):
            dest = cache_dir / f"company_{year}_q{q}.idx"
            fetched = False
            if not dest.exists():
                try:
                    sleep(0.3)
                    dest.write_bytes(_get(INDEX_URL.format(year=year, q=q)))
                    fetched = True
                except Exception as exc:  # noqa: BLE001 — future quarters 404
                    if (year, q) >= current_quarter:
                        logger.debug("EDGAR %s Q%s unavailable (%s)", year, q, exc)
                    else:
                        # A historical quarter exists; failing to fetch it
                        # is an outage, and eras built without it shift
                        # first/last years and change era-gate outcomes.
                        logger.warning(
                            "EDGAR %s Q%s fetch failed (%s)", year, q, exc
                        )
                        failed_quarters.append(f"{year}Q{q}")
                    continue
            for name, cik in _parse_idx(dest):
                norm = normalized_employer(_edgar_name(name))
                if not norm:
                    continue
                span = seen[norm].setdefault(cik, [year, year])
                span[0], span[1] = min(span[0], year), max(span[1], year)
            if fetched and not keep_cache:
                dest.unlink(missing_ok=True)
        logger.info("EDGAR %s indexed (%d names so far)", year, len(seen))

    if failed_quarters:
        # Refusing to write beats overwriting the good committed reference
        # with one whose eras are quietly wrong.
        raise RuntimeError(
            f"EDGAR refresh incomplete: {len(failed_quarters)} historical "
            f"quarter(s) failed ({', '.join(failed_quarters[:8])}"
            + ("…" if len(failed_quarters) > 8 else "")
            + "); the reference file was not rewritten"
        )

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

    # Two ways the reference shows that a one-word name is an ordinary word
    # rather than an identity: several listed companies build their names on
    # it ("United", "General"), or a great many registrants of any kind do
    # ("Bridge" heads 280 reference names, "Commerce" 111, "Advance" 71).
    # Coined names sit far below both: Chipotle heads 1, ChargePoint 2,
    # Arista 16, Goodyear 22.
    GENERIC_EXTENSIONS = 3
    COMMON_TOKEN_NAMES = 40

    def _is_weak_word(self, name: str, year: int | None) -> bool:
        """True if a one-word name is too common to identify a company.

        Some shell registers under the bare word — there is a filer named
        exactly "American" — and a WARN notice from an employer called
        "American" means no such thing. Multi-word names are never treated
        this way; distinctiveness comes from the combination.
        """
        if " " in name:
            return False
        return (
            len(self.by_first_token.get(name, ())) >= self.COMMON_TOKEN_NAMES
            or len(self._listed_extensions(name, year)) >= self.GENERIC_EXTENSIONS
        )

    def _post_era_cik(self, name: str, year: int | None):
        """The company a name belonged to after it stopped being filed under.

        A registrant that went private or was acquired keeps its identity:
        David's Bridal deregistered in 2007 and still filed WARN notices in
        2023. Relaxing the era window forward is safe because a later
        company that took the name over would appear in the reference with
        its own era, breaking the single-CIK requirement below.

        Relaxing backward is not safe and is not done — EDGAR begins in
        1993, so a name with no earlier entry may simply predate the index.
        Midway Airlines of Chicago failed in 1991; the reference knows only
        the unrelated North Carolina airline that took the name in 1997.
        """
        entries = self.by_name.get(name, ())
        if year is None or len({c for c, _, _, _ in entries}) != 1:
            return None
        cik, _, last_year, ticker = entries[0]
        return (cik, ticker, "exact:post-era") if year > last_year else None

    # Generic tokens a registrant's legal name carries and a WARN filing
    # drops: "Amazon" for AMAZON COM, "TeleTech" for TELETECH HOLDINGS.
    # Deliberately short — anything descriptive ("Cessna" vs "Cessna Tina")
    # would match unrelated entities, most of them individuals.
    _SUFFIX_TOKENS = frozenset(
        {"com", "holdings", "holding", "group", "industries", "international",
         "enterprises", "worldwide", "brands", "technologies", "stores"}
    )

    # Shorter than the fuzzy threshold: this rule needs the whole name to
    # match exactly, so it tolerates "Amazon" where fuzzy spelling cannot.
    # Still long enough to keep initialisms ("UPS", "AMC") out.
    MIN_SUFFIX_LEN = 6

    def _suffix_cik(self, name: str, year: int | None):
        """One candidate that is the name plus a single generic token."""
        if len(name) < self.MIN_SUFFIX_LEN:
            return None
        candidates: dict[int, str] = {}
        for cand in self._extensions(name):
            tail = cand[len(name) + 1:]
            if tail not in self._SUFFIX_TOKENS:
                continue
            for cik, ticker in self._era_ciks(cand, year):
                candidates[cik] = ticker
        if len(candidates) != 1:
            return None
        cik, ticker = next(iter(candidates.items()))
        return (cik, ticker, "suffix")

    def _extensions(self, name: str):
        """Reference names that begin with this name plus more words."""
        for cand in self.by_first_token.get(name.split(" ", 1)[0], ()):
            if cand.startswith(name + " "):
                yield cand

    def _listed_extensions(self, name: str, year: int | None) -> dict[int, str]:
        """Listed registrants whose names extend this one, by CIK.

        A notice says "Capital One" or "Peloton"; the SEC knows them as
        CAPITAL ONE FINANCIAL and PELOTON INTERACTIVE, while the bare names
        belong to a 2005 securitization vehicle and an unrelated 2023
        filer. Requiring a ticker is what separates the operating company
        from the shells: only one entity in a corporate family is listed,
        so this stays unique where a plain prefix search would not.

        More than one result is itself informative — it means the name is a
        common word ("United", "Delta") rather than a company's, so the
        weaker rules must not guess at it either.
        """
        return {
            cik: ticker
            for cand in self._extensions(name)
            for cik, ticker in self._era_ciks(cand, year)
            if ticker
        }

    def ticker_for(self, cik: int) -> str | None:
        """The ticker recorded for a CIK under any of its names."""
        for entries in self.by_name.values():
            for entry_cik, _, _, ticker in entries:
                if entry_cik == cik and ticker:
                    return ticker
        return None

    def candidates(self, employer_name: str | None, year: int | None) -> list[dict]:
        """Registrants this name plausibly means, when nothing was matched.

        Every rule in match() ends in a yes or a no, and a no throws away
        what it saw. This recovers it: the registrants a rule considered
        and the gate that stopped it, so a near-miss can be adjudicated
        later rather than silently lost. Returns nothing for names that
        matched — those need no review — and never influences matching.
        """
        norm = normalized_employer(employer_name)
        if not norm or self.match(employer_name, year) is not None:
            return []

        out: list[dict] = []

        def add(cik, ticker, name, rejected_by, note=""):
            out.append({
                "cik": cik, "ticker": ticker or "", "candidate_name": name,
                "rejected_by": rejected_by, "note": note,
            })

        weak = self._is_weak_word(norm, year)
        for cik, ticker in self._era_ciks(norm, year):
            add(cik, ticker, norm,
                "weak-word" if weak else "ambiguous-exact",
                "name is a common word" if weak
                else "several registrants share this name in this era")

        for cand in self._extensions(norm):
            for cik, ticker in self._era_ciks(cand, year):
                if ticker:
                    add(cik, ticker, cand, "ambiguous-extension",
                        "one of several listed companies extending the name")

        # A name whose only exact holder predates it — the direction the
        # era rule refuses, since EDGAR cannot see before 1993.
        entries = self.by_name.get(norm, ())
        if year is not None and len({c for c, _, _, _ in entries}) == 1:
            cik, first_year, last_year, ticker = entries[0]
            if year < first_year:
                add(cik, ticker, norm, "pre-era",
                    f"registrant filed under this name {first_year}-{last_year}")

        if len(norm) >= MIN_FUZZY_LEN:
            for cand in self.by_first_token.get(norm.split(" ", 1)[0], ()):
                jw = jellyfish.jaro_winkler_similarity(norm, cand)
                if jw < FUZZY_REVIEW_MIN or cand == norm:
                    continue
                compatible = _tokens_compatible(norm, cand)
                for cik, ticker in self._era_ciks(cand, year):
                    add(cik, ticker, cand,
                        "near-spelling" if compatible else "incompatible-tokens",
                        f"jaro-winkler {jw:.3f}")

        # Same registrant reached by several routes is still one candidate.
        best: dict[int, dict] = {}
        for row in out:
            best.setdefault(row["cik"], row)
        return list(best.values())

    def match(self, employer_name: str | None, year: int | None):
        """Return (cik, ticker, method) or None."""
        norm = normalized_employer(employer_name)
        if not norm:
            return None
        # Keyed on the base name too, not just the normalized one: two
        # spellings can normalize alike ("Inc. - Olive", "Inc. -Olive")
        # while only one of them has a site qualifier to set aside, and
        # they must not share a cached answer or its method label.
        base = base_employer(employer_name)
        key = (norm, normalized_employer(base), year)
        if key in self._cache:
            return self._cache[key]
        result = None

        weak = self._is_weak_word(norm, year)
        exact = self._era_ciks(norm, year)
        if len({c for c, _ in exact}) == 1 and (exact[0][1] or not weak):
            # A listed registrant may legitimately be named for a common
            # word (Apple); an unlisted one of the same name is a shell.
            cik, ticker = exact[0]
            result = (cik, ticker, "exact")
        elif exact:
            # Several registrants share the name in this era — a holding
            # company and its financing subsidiary, or an operating company
            # and its post-reorganization successor. If exactly one is
            # listed, that is the company the notice means (ARAMARK's 2014
            # notices belong to ARMK, not the pre-IPO shell).
            listed = {c for c, t in exact if t}
            if len(listed) == 1:
                cik = next(iter(listed))
                result = (cik, dict(exact)[cik], "exact:listed")
        elif not weak and len(norm) >= self.MIN_SUFFIX_LEN:
            # No in-era registrant holds the name exactly. A live listed
            # company outranks a dormant exact namesake: "Capital One" means
            # COF, not the 2005 vehicle registered under the bare name.
            listed_ext = self._listed_extensions(norm, year)
            if len(listed_ext) == 1:
                cik, ticker = next(iter(listed_ext.items()))
                result = (cik, ticker, "listed-extension")
            elif listed_ext:
                # Several listed companies extend the name (AMAZON COM and
                # AMAZON HOLDCO). One that adds only a generic word is the
                # same company under its legal name; anything else is a
                # different company that happens to share a first word.
                narrowed = {
                    cik: tk for cik, tk in listed_ext.items()
                    if any(
                        cand[len(norm) + 1:] in self._SUFFIX_TOKENS
                        and cik in dict(self._era_ciks(cand, year))
                        for cand in self._extensions(norm)
                    )
                }
                if len(narrowed) == 1:
                    cik, ticker = next(iter(narrowed.items()))
                    result = (cik, ticker, "listed-extension")
            else:
                result = self._suffix_cik(norm, year) or self._post_era_cik(norm, year)

        if result is None and not exact and len(norm) >= MIN_FUZZY_LEN:
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

        if result is None:
            # The name may identify a site rather than a company: retry
            # without the qualifier, and say so, since a base match is a
            # weaker claim than one the filed name supports outright.
            if base:
                hit = self.match(base, year)
                if hit:
                    result = (hit[0], hit[1], f"{hit[2]}:base")

        self._cache[key] = result
        return result
