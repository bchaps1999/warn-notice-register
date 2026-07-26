"""Nonprofit identity from the IRS exempt-organization Business Master File.

A large share of WARN filers — hospitals, universities, social-service
agencies, school districts, transit authorities — are 501(c)
organizations with no SEC CIK, so the EDGAR and CIK-keyed Wikidata tiers
never reach them. The IRS publishes the whole exempt-organization roster
as four regional CSVs (EIN, name, city, state, subsection, NTEE activity
code), which is the same data ProPublica's Nonprofit Explorer serves —
but joined locally, so matching is deterministic and needs four requests
rather than one per employer.

The join is by name, so it is gated the way the Wikidata label tier is —
a wrong EIN poisons every downstream join, while a missing one costs only
enrichment:

  1. the organization's name must equal the employer name after the same
     normalization (cleanco suffix stripping, punctuation folding),
  2. its state must be one the employer actually filed notices in — the
     decisive gate for chapter organizations ("Goodwill Industries"
     exists dozens of times over, once per state),
  3. exactly one EIN may survive; ties match nothing. Hospital systems
     routinely file a dozen affiliates under one name, and there is no
     way to tell from a WARN notice which affiliate employed the workers.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import urllib.request
from collections import defaultdict
from pathlib import Path
from time import sleep

logger = logging.getLogger("warnlive")

BMF_URLS = [f"https://www.irs.gov/pub/irs-soi/eo{n}.csv" for n in (1, 2, 3, 4)]
ORG_URL = "https://projects.propublica.org/nonprofits/organizations/{ein}"
USER_AGENT = (
    "warn-notice-register/1.0 (https://github.com/bchaps1999/warn-notice-register)"
)
PATH = Path("data/reference/nonprofits.csv.gz")
FIELDS = ["normalized_name", "ein", "name", "state", "ntee", "subsection"]

# NTEE major group -> NAICS. Sector-level except where the group maps to a
# single industry: schools (B2x), colleges (B4x/B5x), hospitals (E2x),
# nursing homes (E91). NTEE is an activity taxonomy, not an industry one,
# so anything finer would be false precision.
_NTEE_NAICS = {
    "A": "71", "B": "61", "C": "81", "D": "81", "E": "62", "F": "62",
    "G": "62", "H": "54", "I": "81", "J": "62", "K": "62", "L": "62",
    "M": "62", "N": "71", "O": "62", "P": "62", "Q": "81", "R": "81",
    "S": "81", "T": "81", "U": "54", "V": "54", "W": "81", "X": "81",
    "Y": "52",
}
_NTEE_EXACT = {
    "B2": "6111",   # elementary and secondary schools
    "B4": "6113",   # colleges and universities
    "B5": "6113",
    "E2": "622",    # hospitals
    "E9": "623",    # nursing and residential care
}


def naics_from_ntee(ntee: str | None) -> str | None:
    """NAICS code implied by an NTEE code, or None."""
    if not ntee:
        return None
    code = ntee.strip().upper()
    if len(code) >= 2 and code[1].isdigit():
        exact = _NTEE_EXACT.get(code[:2])
        if exact:
            return exact
    return _NTEE_NAICS.get(code[:1])


def load(path: Path = PATH) -> dict[str, dict]:
    """normalized_name -> matched organization."""
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as fh:
        return {r["normalized_name"]: r for r in csv.DictReader(fh) if r["ein"]}


def _bmf_rows(url: str):
    """Stream one Business Master File CSV; never cached to disk (the four
    together are a few hundred MB and only a few thousand rows are kept)."""
    sleep(0.5)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        yield from csv.DictReader(io.TextIOWrapper(resp, encoding="latin-1"))


def employer_states(conn, matcher=None) -> dict[str, set[str]]:
    """Normalized employer name -> states it filed notices in, for
    employers the CIK tier does not already identify."""
    from warnlive.normalize.engine import normalized_employer

    out: dict[str, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT employer_name, state, "
        "       substr(COALESCE(notice_date, effective_date), 1, 4) AS y "
        "FROM notices"
    ):
        norm = normalized_employer(r["employer_name"])
        if not norm:
            continue
        if matcher and matcher.match(r["employer_name"], int(r["y"]) if r["y"] else None):
            continue
        out[norm].add(r["state"])
    return out


def refresh(conn, out_path: Path = PATH) -> int:
    """Join our employer names against the IRS exempt-organization roster."""
    from warnlive.enrich.edgar import REFERENCE_PATH, Matcher
    from warnlive.normalize.engine import normalized_employer

    wanted = employer_states(conn, Matcher() if REFERENCE_PATH.exists() else None)
    logger.info("nonprofits: %d CIK-less employer names to look for", len(wanted))

    # normalized name -> ein -> row (same EIN listed twice is not ambiguity)
    hits: dict[str, dict[str, dict]] = defaultdict(dict)
    scanned = 0
    for url in BMF_URLS:
        for row in _bmf_rows(url):
            scanned += 1
            state = (row.get("STATE") or "").strip()
            norm = normalized_employer(row.get("NAME"))
            if not norm or norm not in wanted or state not in wanted[norm]:
                continue
            ein = (row.get("EIN") or "").strip()
            hits[norm][ein] = {
                "normalized_name": norm,
                "ein": ein,
                "name": (row.get("NAME") or "").strip(),
                "state": state,
                "ntee": (row.get("NTEE_CD") or "").strip(),
                "subsection": (row.get("SUBSECTION") or "").strip(),
            }
        logger.info("nonprofits: %s scanned (%d rows total)", url.rsplit("/", 1)[-1], scanned)

    matched = {n: next(iter(e.values())) for n, e in hits.items() if len(e) == 1}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for norm in sorted(matched):
            writer.writerow(matched[norm])
    logger.info(
        "nonprofits: %d matched, %d ambiguous (multiple EINs), of %d rows scanned",
        len(matched), len(hits) - len(matched), scanned,
    )
    return len(matched)
