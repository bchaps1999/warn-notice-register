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
from pathlib import Path

_CODE = re.compile(r"\d{2,6}")

# Census 1987-SIC -> 1997-NAICS concordance, distilled: each SIC resolved
# to the longest NAICS prefix its candidate mappings agree on (unique code,
# else common prefix, else sector group like 31-33); ambiguous SICs are
# omitted. Source: census.gov/naics/concordances/1987_SIC_to_1997_NAICS.xls
SIC_NAICS_PATH = Path("data/reference/sic_naics.csv.gz")


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
    industry text. Keys containing "industry" yield the text. Basis is
    "source" for numeric codes, "sector-name" for codes derived from an
    official sector label.
    """
    industry = naics = basis = None
    for key, value in raw.items():
        text = str(value).strip()
        if not text or text.lower() in ("nan", "none", "n/a"):
            continue
        kl = key.lower()
        if "naics" in kl:
            if "description" in kl:
                if industry is None:
                    industry = text
            elif naics is None:
                m = _CODE.search(text)
                if m:
                    naics, basis = m.group(0), "source"
        elif "industry" in kl and industry is None:
            industry = text
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
