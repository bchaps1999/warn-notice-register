"""Mississippi transformer: split the site out of the PDF's company cell.

The MDES quarterly PDFs print company, city and county in one cell
("Milwaukee Tool Clinton (Hinds)"), so the scraper's ``company`` column
carries the site and ``county`` is empty on most rows. When the cell ends in
"<City> (<County>)", the county is a Mississippi county and the city is a
Mississippi place name, the employer is the text before the city and the
location is "<City> (<County>)" as filed. Otherwise the cell is left whole.

Some quarterly PDFs draw the date column too wide, so the PDF parser puts
the leading words of the next cell into the date cell: "4/17/2026  Aramark"
with company "Services, Inc", or "6/15/2026  WARN – Due" with reason
"Businesses Circumstances...". When a date cell is a date followed by text
that is not another date, the date is read from the leading token and the
spilled text is put back in front of the next cell (company after the notice
date). The company then goes through the site split above.

The raw cells stay in raw_extra; details.extract records the split and the
legacy key fields so existing notices keep their dedupe identity. Reads the
Census place roster in data/reference/places.csv.gz; no network access.
"""

from __future__ import annotations

import csv
import gzip
import re
from functools import lru_cache
from pathlib import Path

from warn_transformer.transformers.ms import Transformer as UpstreamTransformer

_ROSTER = Path(__file__).resolve().parents[3] / "data" / "reference" / "places.csv.gz"
_TRAILING = re.compile(r"^(?P<head>.*\S)\s*\(\s*(?P<county>[^()]+?)\s*\)\s*$", re.S)
_STATUS = re.compile(r"\s+(?:city|town|village|county|cdp)$", re.I)


_NAME_FRAGMENTS = frozenset({
    "inc", "llc", "corp", "corporation", "co", "company", "ltd", "lp", "llp",
    "incorporated", "chaininc", "solutions", "system", "systems", "services",
    "group", "holdings",
})


def _fold(name: str) -> str:
    text = re.sub(r"\bst\.?(?=\s)", "saint", name.lower())
    return re.sub(r"[^a-z0-9]+", "", text)


@lru_cache(maxsize=1)
def _roster() -> tuple[frozenset[str], frozenset[str]]:
    counties, places = set(), set()
    with gzip.open(_ROSTER, "rt", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["state"] != "MS":
                continue
            name = _fold(_STATUS.sub("", row["name"]))
            (counties if row["kind"] == "county" else places).add(name)
    return frozenset(counties), frozenset(places)


def split_company_place(company: str | None) -> tuple[str, str] | None:
    """(employer, "City (County)") when the cell's tail is unambiguous."""
    text = " ".join((company or "").split())
    match = _TRAILING.match(text)
    if not match:
        return None
    counties, places = _roster()
    county = match.group("county")
    if _fold(county) not in counties:
        return None
    words = match.group("head").split()
    for size in (4, 3, 2, 1):
        if len(words) <= size:
            continue
        city = " ".join(words[-size:])
        if _fold(city) in places:
            employer = " ".join(words[:-size]).rstrip(" ,-–—")
            if not any(ch.isalpha() for ch in employer):
                return None
            # A PDF line break can leave only the name's tail ("Inc.",
            # "Solutions") on this line; splitting it off would publish that
            # fragment as the employer, so keep the filed cell whole.
            if _fold(employer) in _NAME_FRAGMENTS:
                return None
            return employer, f"{city} ({county})"
    return None


_SPILL = re.compile(r"^\s*(?P<date>\d{1,2}/\d{1,2}/\d{2,4})\s+(?P<spill>\S.*?)\s*$", re.S)
_DATE_ONLY = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def date_spill(value: str | None) -> tuple[str, str] | None:
    """(date, spilled text) for a date cell carrying the next cell's words.

    A second date ("08/31/2023 09/01/2023") is not a spill.
    """
    match = _SPILL.match(value or "")
    if not match or _DATE_ONLY.fullmatch(match.group("spill")):
        return None
    if not any(ch.isalpha() for ch in match.group("spill")):
        return None
    return match.group("date"), " ".join(match.group("spill").split())


def filed_company(row: dict) -> str:
    """The company cell with any words spilled into the notice-date cell."""
    company = row.get("company") or ""
    spill = date_spill(row.get("date_notice"))
    if not spill:
        return company
    return " ".join(f"{spill[1]} {company}".split())


def spill_details(row: dict) -> dict:
    """source_details naming the date cells whose spill was put back."""
    fields = {}
    for date_field, next_field in (("date_notice", "company"), ("date_effective", "reason")):
        spill = date_spill(row.get(date_field))
        if spill:
            fields[date_field] = {"source_text": row.get(date_field),
                                  "date_text": spill[0], "spill_to": next_field}
    return {"date_cell_spill": {"rule": "ms_date_cell_spill_v1", "fields": fields}} \
        if fields else {}


def _date_cell(name: str):
    def read(row: dict) -> str:
        spill = date_spill(row.get(name))
        return spill[0] if spill else row.get(name, "")
    return read


def split_row(row: dict) -> tuple[str, str] | None:
    """The split for a scraped row; rows with their own county column
    (newer reports) are left as filed."""
    if (row.get("county") or "").strip():
        return None
    return split_company_place(filed_company(row))


def _company(row: dict) -> str:
    split = split_row(row)
    return split[0] if split else filed_company(row)


def _location(row: dict) -> str:
    split = split_row(row)
    return split[1] if split else row.get("county", "")


class Transformer(UpstreamTransformer):
    """Transform Mississippi raw data for consolidation."""

    fields = dict(UpstreamTransformer.fields, company=_company, location=_location,
                  notice_date=_date_cell("date_notice"),
                  effective_date=_date_cell("date_effective"))
