"""Industry detail derived from sources' own raw fields.

Seventeen states publish industry text and/or NAICS codes in their raw
rows (IL "NAICS Codes", MD "NAICS Code", FL/IA/MN "Industry", IN
"Description of Work/Industry", ...). The canonical schema never carried
them; this derives them from the raw_extra preserved with each notice
version, at export/site-build time — the DB keeps only source values.
"""

from __future__ import annotations

import csv
import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

_CODE = re.compile(r"\d{2,6}")

# Valid NAICS sector prefixes. Sources sometimes label a pre-2002 SIC code
# as "NAICS" (Wisconsin's 2001 log mixes both); a code whose first two
# digits are not a sector is not NAICS, and a 4-digit one is almost always
# SIC, which the concordance can still resolve.
_NAICS_SECTORS = {
    "11", "21", "22", "23", "31", "32", "33", "42", "44", "45", "48", "49",
    "51", "52", "53", "54", "55", "56", "61", "62", "71", "72", "81", "92",
}

# Census 1987-SIC -> 1997-NAICS concordance, distilled: each SIC resolved
# to the longest NAICS prefix its candidate mappings agree on (unique code,
# else common prefix, else sector group like 31-33); ambiguous SICs are
# omitted. Source: census.gov/naics/concordances/1987_SIC_to_1997_NAICS.xls
SIC_NAICS_PATH = Path("data/reference/sic_naics.csv.gz")

# Sectors decided by adjudication rather than published by anybody. Read here
# with the other reference files; written by warnlive adjudicate industry.
OVERRIDES_PATH = Path("data/reference/industry_overrides.csv")

# Which question a code answers, per basis.
#
# NAICS classifies establishments, not companies, and a large employer runs
# establishments in several sectors at once: Caterpillar's plant is 31-33 and
# its parts depot is 42, ArcelorMittal's mill is 31-33 and its headquarters is
# 55 — the sector whose name is literally Management of Companies. States code
# the site a notice is about; the SEC and the IRS code the organization. Where
# a notice carries both, the two agree about four times in five, and the fifth
# is not an error but two correct answers to different questions.
#
# So the level is recorded and the conflict is left standing. Flattening them
# would destroy the distinction rather than resolve it, and a consumer
# studying which industries are shedding jobs wants the site, while one
# studying which firms are shrinking wants the company.
_LEVEL_BY_BASIS = {
    "source": "establishment",         # the state's own code for the site
    "sector-name": "establishment",    # the state's own sector label
    "sic-crosswalk": "establishment",  # a SIC the state published
    "adjudicated": "establishment",    # the model is asked about the site
    # Inherited from another notice of the same employer. An establishment
    # code, but observed at a different establishment — which is why the
    # basis says so and this tier ranks last.
    "employer": "establishment",
    "sec-sic": "enterprise",           # the SIC the SEC assigned the filer
    "ntee": "enterprise",              # the IRS activity code for the org
    "parent-sic": "enterprise",        # the parent company's industry
}


def naics_level(basis: str | None) -> str | None:
    """Whether a code describes the site or the company behind it."""
    return _LEVEL_BY_BASIS.get(basis or "")


def load_industry_overrides(path: Path = OVERRIDES_PATH) -> dict[str, str]:
    """normalized employer name -> adjudicated NAICS sector.

    Kept separate from the published-code path on purpose: an entry here is
    a conclusion about an employer, not a code a state printed, and the
    export says so by labelling the basis "adjudicated".
    """
    if not path.exists():
        return {}
    with open(path, newline="") as fh:
        return {
            r["normalized_name"]: r["naics"]
            for r in csv.DictReader(fh)
            if r.get("normalized_name") and r.get("naics")
        }


# "sic" as a standalone word: "SIC", "SIC Code" — never "music", "basic".
_SIC_KEY = re.compile(r"\bsic\b")


@lru_cache(maxsize=None)
def load_sic_naics(path: Path = SIC_NAICS_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    with gzip.open(path, "rt") as fh:
        return {r["sic"]: r["naics"] for r in csv.DictReader(fh)}

# Official NAICS sector names -> 2-digit sector codes. FL/IA/MN publish
# these labels verbatim; a handful of common variants are included. Used
# only when the source provides no numeric code — the emitted value is a
# 2-digit sector, distinguishable from 4-6 digit source codes by length.
_SECTOR_BY_NAME = {
    "agriculture, forestry, fishing and hunting": "11",
    "mining, quarrying, and oil and gas extraction": "21",
    "mining": "21",
    "utilities": "22",
    "construction": "23",
    "manufacturing": "31-33",
    "wholesale trade": "42",
    "retail trade": "44-45",
    "retail": "44-45",
    "transportation and warehousing": "48-49",
    "transportation & warehousing": "48-49",
    "information": "51",
    "finance and insurance": "52",
    "finance & insurance": "52",
    "real estate and rental and leasing": "53",
    "professional, scientific, and technical services": "54",
    "professional, scientific and technical services": "54",
    "management of companies and enterprises": "55",
    "administrative and support and waste management and remediation services": "56",
    "administrative and support and waste management": "56",
    "educational services": "61",
    "health care and social assistance": "62",
    "healthcare and social assistance": "62",
    "arts, entertainment, and recreation": "71",
    "arts, entertainment and recreation": "71",
    "accommodation and food services": "72",
    "accommodation & food services": "72",
    "other services (except public administration)": "81",
    "other services": "81",
    "public administration": "92",
}
_WS_RUN = re.compile(r"\s+")


# The 20 NAICS sectors, in official order. Three are ranges because the
# standard itself groups them that way; codes derived from a sector name
# already arrive in range form, codes from a source arrive as digits, and
# sector_of() reconciles the two so the site can group either.
SECTOR_LABELS: dict[str, str] = {
    "11": "Agriculture, forestry, fishing and hunting",
    "21": "Mining, quarrying, oil and gas",
    "22": "Utilities",
    "23": "Construction",
    "31-33": "Manufacturing",
    "42": "Wholesale trade",
    "44-45": "Retail trade",
    "48-49": "Transportation and warehousing",
    "51": "Information",
    "52": "Finance and insurance",
    "53": "Real estate, rental and leasing",
    "54": "Professional, scientific and technical services",
    "55": "Management of companies",
    "56": "Administrative, support and waste services",
    "61": "Educational services",
    "62": "Health care and social assistance",
    "71": "Arts, entertainment and recreation",
    "72": "Accommodation and food services",
    "81": "Other services",
    "92": "Public administration",
}
_SECTOR_OF_PREFIX = {
    p: sector
    for sector in SECTOR_LABELS
    for p in (
        [sector] if "-" not in sector
        else [f"{n:02d}" for n in range(int(sector[:2]), int(sector[-2:]) + 1)]
    )
}


def sector_of(naics: str | None) -> str | None:
    """The NAICS sector a code belongs to, as a SECTOR_LABELS key."""
    if not naics:
        return None
    return _SECTOR_OF_PREFIX.get(naics[:2])


def sector_from_text(industry: str | None) -> str | None:
    """2-digit NAICS sector for an official sector name; None otherwise."""
    if not industry:
        return None
    return _SECTOR_BY_NAME.get(_WS_RUN.sub(" ", industry).strip().lower())


def extract_industry(raw: dict) -> tuple[str | None, str | None, str | None]:
    """Return (industry_text, naics_code, naics_basis) from a source raw row.

    Keys containing "naics" yield the code (first 2-6 digit run; handles
    float-formatted spreadsheet values like "322233.0" and multi-code
    lists) unless they are descriptions ("NAICS Description"), which are
    industry text. Keys containing "sic" yield a code resolved through the
    concordance. Keys containing "industry" yield the text. Basis is
    "source" for numeric NAICS, "sic-crosswalk" for concordance results,
    "sector-name" for codes derived from an official sector label.
    """
    industry = naics = basis = sic = None
    for key, value in raw.items():
        text = str(value).strip()
        if not text or text.lower() in ("nan", "none", "n/a"):
            continue
        kl = key.lower()
        if "naics" in kl or _SIC_KEY.search(kl):
            if "description" in kl:
                if industry is None:
                    industry = text
                continue
            m = _CODE.search(text)
            if not m:
                continue
            code = m.group(0)
            if "naics" in kl and code[:2] in _NAICS_SECTORS:
                if naics is None:
                    naics, basis = code, "source"
            elif sic is None and len(code) == 4:
                # Only a full 4-digit code can be read as SIC: padding a
                # short one ("79") into "0079" would hit an unrelated
                # industry in the concordance.
                sic = code
        elif "industry" in kl and industry is None:
            industry = text
    if naics is None and sic is not None:
        naics = load_sic_naics().get(sic)
        if naics:
            basis = "sic-crosswalk"
    if naics is None:
        naics = sector_from_text(industry)
        if naics:
            basis = "sector-name"
    return industry, naics, basis


def industry_from_fields_json(
    fields_json: str | None,
) -> tuple[str | None, str | None, str | None]:
    """extract_industry over a notice version's stored fields_json."""
    if not fields_json:
        return None, None, None
    try:
        raw = json.loads(json.loads(fields_json).get("raw_extra") or "{}")
    except (TypeError, ValueError):
        return None, None, None
    if not isinstance(raw, dict):
        return None, None, None
    return extract_industry(raw)
