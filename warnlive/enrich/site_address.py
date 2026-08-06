"""Surface layoff-site street addresses into notices.site_address.

Two sources, no new scraping:

1. Raw fields the normalizers never mapped — several states' scrapes carry
   a street address in raw_extra (PA addressfull, KY/AZ/ME/VT/KS/MI address,
   IA Address Line 1, GA First Location Address) that only ever reached the
   version JSON.
2. States whose location column already IS a street address (IL, MD, NC,
   LA, partial CT) — copied over so consumers have one canonical column.

site_address is deliberately separate from location: location feeds the
dedupe key, and daily scrapes keep producing the city-only form, so
rewriting it would fork every key and duplicate the state on next run.

Portal-sourced addresses are sometimes the employer's corporate HQ rather
than the layoff site (a Kansas notice listing a Cincinnati, OH address),
so a value naming a different state is rejected.
"""

from __future__ import annotations

import re

# state -> raw_extra keys to try, in preference order
RAW_ADDRESS_KEYS: dict[str, list[str]] = {
    "PA": ["addressfull"],
    "KY": ["address"],
    "AZ": ["address"],
    "IA": ["Address Line 1"],
    "GA": ["First Location Address", "Company Address"],
    "ME": ["address"],
    "VT": ["address"],
    "KS": ["address"],
    "MI": ["address"],
    "DE": ["address"],
}

# location column already holds a street address in these states
LOCATION_IS_ADDRESS = {"IL", "MD", "NC", "LA", "CT"}

# A street address starts with a street number ("1225 W Lake...", "2410
# GA-32..."). Requiring a street-type suffix instead rejects too much real
# data — "1111 East McDowell", "224 E. Broadway" — so the number anchors it
# and a junk blacklist handles the rest.
_STREETISH = re.compile(r"^\d{1,6}[\w./-]*\s+[A-Za-z]")
_JUNK = re.compile(
    r"\bP\.?\s?O\.?\s*Box\b|\d+\s+(Stores?|Locations?|Sites?|Counties)\b"
    r"|no physical site|remote work|\bN/?A\b|\bUnknown\b|\bSeveral\b"
    r"|\bMultiple\b|\bVarious\b",
    re.I,
)
_WS = re.compile(r"\s+")

_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def clean_address(value: str | None, state: str) -> str | None:
    """Return a usable street address, or None.

    Accepts a value that looks like a street address and does not name a
    different state. An address with no state token at all is accepted —
    most bare "123 Main St." values are in-state; the guard exists for the
    portal HQ case, which always spells out the foreign state or its abbrev.
    """
    if not value:
        return None
    text = _WS.sub(" ", str(value)).strip(" ,;")
    if not text or not _STREETISH.search(text) or _JUNK.search(text):
        return None
    # Only the tail names the state ("... Cincinnati, Ohio 45202"); checking
    # the whole string would reject street names like "Washington Ave".
    tail = text.rsplit(",", 1)[-1] if "," in text else " ".join(text.split()[-3:])
    for abbrev, name in _STATE_NAMES.items():
        if abbrev == state:
            continue
        if re.search(rf"\b{abbrev}\b(?=[\s,.]|\d|$)", tail) or \
                re.search(rf"\b{name}\b", tail, re.I):
            return None
    return text
