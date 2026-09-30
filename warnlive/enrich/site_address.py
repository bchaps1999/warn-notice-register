"""Deterministic, source-derived worksite addresses for notices.site_address.

Address role policy (site_address_surface_v4)
---------------------------------------------
``site_address`` holds a street address only when the source establishes
that the address is where the reported layoff happens.  An address is
exported as ``site_address`` under exactly one of these bases:

``quality_evidence``
    Written by ``warnlive.migrate.quality_evidence`` from a pinned official
    document or an explicitly labeled agency site field (CA, NC, MD).  That
    pass owns every row it has annotated and every row in its states; this
    module never writes those rows.

``labeled_site_field``
    The agency column itself names the affected site: Illinois IEBS
    ``Location Address`` (with ``Location City``/``State``/``Zipcode``),
    the New York DOL dashboard's ``Impacted Site Address``, Georgia's
    ``First Location Address`` when the filing lists exactly one location,
    and the South Carolina report's worksite ``address`` column.

``filed_county_consistent``
    The column is only called "address" (Pennsylvania ``addressfull``), so
    its role is not labeled.  It is accepted only when the address resolves,
    against the Census roster, to the same county the agency filed in its
    own independent ``county`` column.

``fl_company_cell_in_state``
    Florida's ``Company Name`` cell is the company name, its street line(s)
    and a final uppercase ``CITY, FL, ZIP`` line.  The column does not label
    the street line's role, but a sampled check against the agency's notice
    letters found the in-state street line to be the affected worksite in
    every decidable case, and the out-of-state street line to be the
    headquarters or reporting office (see
    docs/fl-address-and-type-codes-2026-09-30.md).  The street line(s) plus
    the final city line are therefore accepted only when the street lines
    name no other state, no non-Florida ZIP, and pass the shape checks
    below.  A cell whose street line is out of state is rejected
    (``names_another_state``), never re-labeled.  The notice's ``location``
    is itself derived from this cell, so the value is a source fact about
    the filing, not an independent geocode.

Every accepted value must also look like one street address (a house
number, no P.O. box, no "multiple/various" text, not two concatenated
street addresses) and must not name another state.  An Illinois row whose
labeled ``Location State`` is another state is rejected: it is an
out-of-state location or mailing address, not an Illinois worksite.

A labeled site cell that opens with a venue or division name ("Bloomingdale's,
1000 Third Avenue", "Aon Center 200 E Randolph Dr") keeps only the street
part, and only when exactly one house-numbered street follows the name; a
number after a unit, terminal, route or box word does not count, and a name
that says remote, P.O. box, care-of or multiple sites blocks it.  A
spelled-out house number ("One Penn Plaza") counts when a street-type word
follows, and is stored as the source spelled it.  The full cell always stays
in raw_extra.  ``apply`` counts these as ``surfaced_after_name_prefix``.

Addresses whose role the source does not label, and for which no
independent filed place exists to check them against, are *not* exported:
Florida cells that do not have the three-part layout above (the older
inline "name street city, FL zip" form), Idaho's ``Address``/``City`` block, the America's JobLink
portals' ``address`` (AZ, KS, ME, VT, MI, DE — Kansas documents these as
sometimes the employer's out-of-state contact), and Missouri's company
``Address``.  They remain in raw_extra; ``apply`` counts them as
``not_surfaced_unlabeled_role`` so the gap is visible.  No address is ever
re-labeled as ``employer_mailing_address``: that column is reserved for a
source that labels a mailing address as such.

``affected_site_address`` is unchanged by this module: it remains the
strict projection of quality evidence (``warnlive.enrich.notice_quality``).

The pass is a pure function of each notice's current version, so it is
idempotent and gives the same result after an offline source replay and
after a live scrape.  site_address is a derived column (it does not create a
notice version); in the states this pass governs, a value it cannot
reproduce is cleared.  ``location`` is never rewritten, because it feeds
stable keys.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

RULE = "site_address_surface_v4"

# Basis values exported as site_address_basis.
BASIS_QUALITY = "quality_evidence"
BASIS_LABELED = "labeled_site_field"
BASIS_COUNTY = "filed_county_consistent"
BASIS_FL_CELL = "fl_company_cell_in_state"
BASIS_UNVERIFIED = "unverified"

# States whose site_address belongs to the pinned quality-evidence pass.
QUALITY_STATES = frozenset({"CA", "NC", "MD"})
# state -> basis of the address this module surfaces there.
SURFACE_STATES: dict[str, str] = {
    "IL": BASIS_LABELED,
    "NY": BASIS_LABELED,
    "GA": BASIS_LABELED,
    "SC": BASIS_LABELED,
    "PA": BASIS_COUNTY,
    "FL": BASIS_FL_CELL,
}
# state -> raw field holding an address whose role the source does not label
# and which has no independent filed place to check it against.  Counted,
# never exported.
UNLABELED_ADDRESS_FIELDS: dict[str, tuple[str, ...]] = {
    "FL": ("Company Name", "COMPANY NAME"),  # inline / non-standard cells only
    "ID": ("Address",),
    "AZ": ("address",),
    "KS": ("address",),
    "ME": ("address",),
    "VT": ("address",),
    "MI": ("address",),
    "DE": ("address",),
    "MO": ("Address",),
}

# Retained for the legacy `surface-addresses` command, which predates the
# role policy above.  Do not use for new code: it treats unlabeled portal
# and company addresses as worksites.
RAW_ADDRESS_KEYS: dict[str, list[str]] = {
    "PA": ["addressfull"],
    "KY": ["address"],
    "AZ": ["address"],
    "IA": ["Address Line 1"],
    "ID": ["Address"],
    "GA": ["First Location Address", "Company Address"],
    "SC": ["address", "Address"],
    "ME": ["address"],
    "VT": ["address"],
    "KS": ["address"],
    "MI": ["address"],
    "DE": ["address"],
}
LOCATION_IS_ADDRESS = {"IL", "MD", "NC", "LA", "CT"}

# A street address starts with a street number ("1225 W Lake...", "2410
# GA-32..."). Requiring a street-type suffix instead rejects too much real
# data — "1111 East McDowell", "224 E. Broadway" — so the number anchors it
# and a junk blacklist handles the rest.
_STREETISH = re.compile(r"^\d{1,6}[\w./-]*\s+(?:\d{1,3}(?:st|nd|rd|th)\s+)?[A-Za-z]", re.I)
_JUNK = re.compile(
    r"\bP\.?\s?O\.?\s*Box\b|\d+\s+(Stores?|Locations?|Sites?|Counties)\b"
    r"|no physical site|remote work|\bN/?A\b|\bUnknown\b|\bSeveral\b"
    r"|\bMultiple\b|\bVarious\b"
    r"|\b\d+\s+(?:[\w-]+\s+){0,3}(?:Locations|Sites|Stores|Offices|Facilities|Branches)\b",
    re.I,
)
_WS = re.compile(r"\s+")
_LIST_JOIN = re.compile(r";\s*and\b|;\s*\d{1,6}\s+[A-Za-z]", re.I)
# "County Road 53", "State Route 9": a numbered road, not a second address.
_ROAD_PREFIX = {"county", "state", "township", "farm", "forest", "fire",
                "ranch", "us", "u.s.", "interstate"}
_ROAD_TYPES = {"street", "st", "avenue", "ave", "road", "rd", "drive", "dr",
               "blvd", "boulevard", "lane", "ln", "parkway", "pkwy", "way",
               "court", "ct", "circle", "cir", "highway", "hwy", "route", "rte"}
_STREET_THEN_NUMBER = re.compile(
    r"(?P<before>\S+)\s+(?P<type>Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Blvd|"
    r"Boulevard|Lane|Ln|Parkway|Pkwy|Way|Court|Ct|Circle|Cir)\.?[,;]?\s+"
    r"(?:(?:and|&)\s+)?\d{1,6}\s+(?:[NSEW]\.?\s+)?(?P<next>[A-Za-z][\w.]*)", re.I)
_STATE_ZIP = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")
# A line opening with a house number ("907-937A Market"), not a floor
# ("23rd Floor").
_HOUSE_LINE = re.compile(r"^\s*,?\s*\d{1,6}(?:-\d{1,6})?[A-Za-z]?\s+[A-Za-z]")
_UNIT_WORDS = {"fl", "floor", "suite", "ste", "unit", "apt", "bldg", "building",
               "room", "rm"}


def _several_addresses(value: str) -> bool:
    """Whether one cell lists more than one street address.

    Two house-numbered lines, two state-plus-ZIP tails, "; and", or a street
    type followed by another house number ("1020 Olympic Dr. 950 Raddant
    Rd.") all mean several sites.  A numbered road is not a second address:
    "72 County Road 53", "2605 Circle 75 Pkwy".
    """
    if _LIST_JOIN.search(value):
        return True
    lines = [line for line in re.split(r"[\n;]", value) if _HOUSE_LINE.search(line)]
    if len(lines) > 1 or len(set(_STATE_ZIP.findall(value))) > 1:
        return True
    for match in _STREET_THEN_NUMBER.finditer(_WS.sub(" ", value)):
        if match["before"].casefold() in _ROAD_PREFIX:
            continue
        if match["next"].strip(".").casefold() in _ROAD_TYPES | _UNIT_WORDS:
            continue
        return True
    return False

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

# The New York dashboard CSV column holding the affected site's address
# (warnlive.migrate.ny_source.HEADERS[4] == "Impacted Site Address").
_NY_DASHBOARD_WIDTH = 12
_NY_SITE_CELL = 4


def clean_address(value: str | None, state: str, *, check_state: bool = True) -> str | None:
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
        if abbrev == state or not check_state:
            continue
        if re.search(rf"\b{abbrev}\b(?=[\s,.]|\d|$)", tail) or \
                re.search(rf"\b{name}\b", tail, re.I):
            return None
    return text


@dataclass(frozen=True)
class Decision:
    """Outcome for one notice: an address with its basis, or a reason."""

    address: str | None
    basis: str | None
    reason: str
    source_field: str | None = None


def _load(value) -> object:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def raw_fields(row) -> object:
    """The current version's raw_extra: a dict, a list of cells, or {}."""
    fields = _load(row.get("fields_json"))
    if not isinstance(fields, dict):
        return {}
    raw = _load(fields.get("raw_extra"))
    return raw if isinstance(raw, (dict, list)) else {}


def _text(value) -> str:
    return _WS.sub(" ", str(value or "")).strip(" ,;")


# A house number that can start the street part of a cell after a name
# prefix: "Bloomingdale's, 1000 Third Avenue", "Bard College 30 Campus Road".
# Only a plain number (optionally "12-14" or "12A") followed by a word; the
# preceding character must end the name ("," ":" "/" "(" or a space).
_HOUSE_START = re.compile(
    r"(?:^|(?<=[\s,:/(]))(\d{1,6}(?:-\d{1,6})?[A-Za-z]?)\s+"
    r"(?=[A-Za-z]|\d{1,3}(?:st|nd|rd|th)\b)", re.I)
# "1300, 1400 and 1420 Chase Ave", "Terminals 2 and 4": a list of numbers.
_NUMBER_LIST = re.compile(
    r"\b\d{1,6}\s*(?:&|\band\b)\s*\d{1,6}\b|\b\d{1,6}\s*,\s*\d{1,6}\s*,\s*\d{1,6}\b", re.I)
# Words after which a number is a unit, gate, road or box number, not a house
# number: "Terminal 4 Jamaica", "Suite 300 Bannockburn", "Route 9W West Park".
_NOT_HOUSE_BEFORE = _UNIT_WORDS | _ROAD_PREFIX | {
    "terminal", "gate", "pier", "hangar", "dock", "door", "box", "code", "no",
    "number", "level", "wing", "lot", "store", "site", "sites", "location",
    "locations", "exit", "plant", "hall", "station", "route", "rt", "rte",
    "highway", "hwy", "road", "rd", "rr", "ramp", "concourse", "dept",
    "department", "division", "unit", "building", "bldg", "mile", "and", "&",
    "terminals", "buildings", "bldgs", "units", "suites", "floors",
    "hangars", "piers", "post", "milepost", "mp", "i", "exit",
}
# A name prefix that is itself a road, box, mailing or remote marker is not a
# venue name: "RT 4, 1300 N Market Street", "Mailing Center ... 50 Industrial".
_BAD_PREFIX = re.compile(
    r"\bc/o\b|\b(?:RR|RT|RTE|Route|Highway|Hwy|Box|Mail\w*|Remote|Multiple|Various|"
    r"Several|Corner|Intersection|Near|Between|Off)\b|\d+\s*(?:&|and)\s*$", re.I)
# "One Penn Plaza", "Two North Riverside Plaza": a spelled-out house number.
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                 "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
_WORD_NUMBER_START = re.compile(r"^(One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\s+(?=[A-Za-z])", re.I)
_STREET_WORD = re.compile(
    r"\b(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd|Lane|Ln|Parkway|"
    r"Pkwy|Way|Court|Ct|Circle|Cir|Place|Pl|Plaza|Center|Centre|Square|Sq|Park|"
    r"Terrace|Ter|Highway|Hwy|Trail|Trl|Pike|Turnpike|Row|Walk|Loop)\b\.?", re.I)


def _street_part(text: str) -> tuple[str, str | None]:
    """The street address inside a labeled site cell, and the name before it.

    Returns ``(text, None)`` when the cell already starts with a house
    number.  A cell that opens with a venue or division name followed by
    exactly one house-numbered street ("Walter Kerr Theatre, 218 West 48th
    Street") yields the street part and the stripped name.  Zero or several
    candidate house numbers, or a prefix that is itself a road, box, mailing
    or remote marker, leave the cell unchanged, so the ordinary checks reject
    it.  The full cell always stays in raw_extra.
    """
    if _STREETISH.search(text) or _NUMBER_LIST.search(text):
        return text, None
    # A spelled-out number opening the cell ("One Penn Plaza") is the house
    # number unless the cell also gives one numbered street, which is then
    # preferred ("One World Financial Center, 200 Liberty Street"; "Four
    # Seasons Hotel, 99 Church St.").
    starts = []
    for match in _HOUSE_START.finditer(text):
        raw_before = text[:match.start()].split()
        before = text[:match.start()].rstrip(" ,:/(-").split()
        word = re.split(r"[-/]", before[-1])[-1].strip(".#:,") if before else ""
        # A unit/road word, a bare "#", or a bare number ("Dist. 60 2325")
        # right before it: not a house number.
        # "Marriot Towne Suites, 38-42 11th Street": a unit word closed by a
        # comma is part of a name, so the number after it still counts.
        if before and ((word.casefold() in _NOT_HOUSE_BEFORE
                        and not raw_before[-1].endswith(","))
                       or not word or raw_before[-1].isdigit()):
            continue
        starts.append(match.start())
    if len(starts) != 1:
        return text, None
    prefix = text[:starts[0]].rstrip(" ,:/(-")
    street = text[starts[0]:]
    # A name, not a stray letter or grid coordinate ("S 3701", "10188E 2150N");
    # one bare word needs a separator ("Broadway, 2085 Broadway", not
    # "ROTUE 51 Payne Drive").
    if (not re.search(r"[A-Za-z]{2,}", prefix) or _BAD_PREFIX.search(prefix)
            or (len(prefix.split()) == 1
                and not re.search(r"[,:/-]\s*$", text[:starts[0]]))):
        return text, None
    head = street.split("  ")[0]
    # Two venues or streets joined ("5700 S Cicero & Terminal 2").
    if re.search(r"&|\band\b", head, re.I):
        return text, None
    # A street name after the number, not "CNA Plaza 42 S".
    words = re.findall(r"[A-Za-z]{3,}", head.split(",")[0])
    if not [w for w in words if w.casefold() not in {"north", "south", "east", "west"}]:
        return text, None
    return street, prefix


def _word_number_ok(text: str) -> str | None:
    """"One Penn Plaza ..." as "1 Penn Plaza ..." for the shape checks, when
    a street-type word follows ("One NYC location" is not an address) and
    no second street is joined on ("One Madison Avenue and Eleven Madison
    Avenue")."""
    match = _WORD_NUMBER_START.match(text)
    if not match or not _STREET_WORD.search(text[match.end():]):
        return None
    if re.search(r"&|\band\b", text.split("  ")[0], re.I):
        return None
    return _NUMBER_WORDS[match[1].casefold()] + " " + text[match.end():]


def _prefix_blocked(text: str) -> bool:
    """A cell whose name part says remote, P.O. box, multiple sites, etc."""
    return bool(_JUNK.search(text) or re.search(r"\bremote\b", text, re.I))


def _checked(value: str, state: str, basis: str, field: str, *,
             prefixed: bool | None = None, check_state: bool = True) -> Decision:
    """Common shape checks for any candidate address.

    A leading venue name is stripped here unless the caller already handled
    it: ``prefixed`` True/False says whether the caller removed one (Illinois
    strips its address component before composing it with the city)."""
    text = _text(value)
    if not text:
        return Decision(None, None, "no_address_in_field", field)
    if _several_addresses(str(value)):
        return Decision(None, None, "multiple_addresses_in_field", field)
    if prefixed is None:
        street, prefix = _street_part(text)
        if prefix is not None and _prefix_blocked(text):
            return Decision(None, None, "not_a_street_address", field)
    else:
        street, prefix = text, ("stripped by caller" if prefixed else None)
    probe = _word_number_ok(street) or street
    cleaned = clean_address(probe, state, check_state=check_state)
    if cleaned is None:
        if _STREETISH.search(probe) and not _JUNK.search(probe):
            return Decision(None, None, "names_another_state", field)
        return Decision(None, None, "not_a_street_address", field)
    # The stored value keeps the source's own spelling ("One Penn Plaza").
    cleaned = _text(street)
    reason = "accepted_after_name_prefix" if prefix is not None else "accepted"
    return Decision(cleaned, basis, reason, field)


def _illinois(raw: object) -> Decision:
    field = "Location Address"
    if not isinstance(raw, dict) or not _text(raw.get(field)):
        return Decision(None, None, "no_labeled_site_field")
    labeled_state = _text(raw.get("Location State")).upper()
    if labeled_state and labeled_state not in {"IL", "ILLINOIS"}:
        return Decision(None, None, "labeled_location_state_not_il", field)
    # Composed from the labeled components in the listing's own order:
    # "334 W North St Momence, IL 60954-1157".  A venue name before the
    # street ("Aon Center 200 E Randolph Dr") is dropped here, on the
    # address component alone; the full cell stays in raw_extra.
    value = _text(raw.get(field))
    if _several_addresses(value):
        return Decision(None, None, "multiple_addresses_in_field", field)
    street, prefix = _street_part(value)
    if prefix is not None:
        if _prefix_blocked(value):
            return Decision(None, None, "not_a_street_address", field)
        value = street
    city = _text(raw.get("Location City"))
    if city:
        value = f"{value} {city}"
    value = f"{value}, {' '.join(p for p in ('IL', _text(raw.get('Location Zipcode'))) if p)}"
    return _checked(value, "IL", BASIS_LABELED, field, prefixed=prefix is not None)


def _new_york(row, raw: object) -> Decision:
    field = "Impacted Site Address"
    if isinstance(raw, list) and len(raw) == _NY_DASHBOARD_WIDTH:
        value = raw[_NY_SITE_CELL]
    elif isinstance(raw, dict) and _text(raw.get(field)):
        value = raw.get(field)
    else:
        return Decision(None, None, "no_labeled_site_field")
    value = str(value or "")
    # The dashboard's site cell is also the listing location (some sources
    # append the Impacted Site County); anything else means the location did
    # not come from this labeled field.
    location = _text(row.get("location")).casefold()
    site = _text(value).casefold()
    county = _text(raw[_NY_SITE_CELL + 1] if isinstance(raw, list)
                   else raw.get("Impacted Site County")).casefold()
    if location and location not in {site, f"{site}, {county}".rstrip(", ")}:
        return Decision(None, None, "location_not_from_labeled_field", field)
    return _checked(value, "NY", BASIS_LABELED, field)


def _georgia(raw: object) -> Decision:
    field = "First Location Address"
    if not isinstance(raw, dict) or not _text(raw.get(field)):
        return Decision(None, None, "no_labeled_site_field")
    listed = [key for key in raw if isinstance(key, str)
              and key.endswith(" Location Address") and _text(raw.get(key))]
    count = _text(raw.get("Number of Locations Associated with WARN Event")).casefold()
    if len(listed) != 1 or (count and count != "one"):
        return Decision(None, None, "multiple_sites_listed", field)
    return _checked(raw[field], "GA", BASIS_LABELED, field)


def _south_carolina(raw: object) -> Decision:
    field = "address"
    if not isinstance(raw, dict) or not _text(raw.get(field)):
        return Decision(None, None, "no_labeled_site_field")
    return _checked(raw[field], "SC", BASIS_LABELED, field)


def _pennsylvania(raw: object, resolver) -> Decision:
    field = "addressfull"
    if not isinstance(raw, dict) or not _text(raw.get(field)):
        return Decision(None, None, "no_address_field")
    checked = _checked(raw[field], "PA", BASIS_COUNTY, field)
    if checked.address is None:
        return checked
    county = _text(raw.get("county"))
    if not county or resolver is None:
        return Decision(None, None, "no_independent_filed_county", field)
    filed = resolver._county_only("PA", county)
    if not filed:
        return Decision(None, None, "filed_county_not_in_roster", field)
    resolved = resolver.resolve("PA", checked.address)
    if not resolved.get("county_fips"):
        return Decision(None, None, "address_place_unresolved", field)
    if resolved["county_fips"] != filed["county_fips"]:
        return Decision(None, None, "address_county_differs_from_filed_county", field)
    return checked


_FL_FINAL_LINE = re.compile(
    r"^(?P<city>[A-Z][A-Z .'\-&/]*[A-Z.]),\s*FL,?\s+(?P<zip>\d{5})(?:-\d{4})?$")
_FL_ZIP = re.compile(r"^3[2-4]\d{3}$")


def _other_state_in(text: str) -> bool:
    """Whether street text names a state other than Florida: state + ZIP, a
    ", XX" abbreviation, or a state name ending a comma-separated part."""
    for match in re.finditer(r"\b([A-Z]{2})\s+\d{5,}", text):
        if match.group(1) != "FL":
            return True
    parts = [part.strip(" .;") for part in text.split(",")]
    for index, part in enumerate(parts):
        for abbrev, name in _STATE_NAMES.items():
            if abbrev == "FL":
                continue
            if (index and part == abbrev) or re.search(rf"\b{name}$", part, re.I):
                return True
    return False


def _florida(raw: object) -> Decision | None:
    """Decision for a Florida three-part company cell, or None when the cell
    has another layout (left to the unlabeled-role count)."""
    field = next((f for f in ("Company Name", "COMPANY NAME")
                  if isinstance(raw, dict) and raw.get(f)), None)
    if field is None:
        return None
    lines = [line.strip() for line in str(raw[field]).split("\n") if line.strip()]
    if len(lines) < 3:
        return None
    final = _FL_FINAL_LINE.match(lines[-1])
    if not final:
        return None
    street_lines = lines[1:-1]
    street = _WS.sub(" ", " ".join(street_lines)).strip(" ,;")
    if not street_lines or not re.search(r"\d", street):
        return Decision(None, None, "no_address_in_field", field)
    if not _FL_ZIP.match(final["zip"]):
        return Decision(None, None, "zip_not_florida", field)
    if any(_other_state_in(line) for line in street_lines) or _other_state_in(street):
        return Decision(None, None, "names_another_state", field)
    # The other-state check above replaces clean_address's tail test, which
    # misreads street names and quadrants ("Washington Avenue", "Road NE").
    checked = _checked(street, "FL", BASIS_FL_CELL, field, check_state=False)
    if checked.address is None:
        return checked
    # A street line that already carries its own city and state ("..., Delray
    # Beach Florida") is kept as filed, without the cell's city appended.
    if re.search(r"\bFlorida\b|\bFL\b", checked.address, re.I):
        return checked
    city = final["city"].strip()
    return Decision(f"{checked.address}, {city}, FL {final['zip']}", BASIS_FL_CELL,
                    checked.reason, field)


def derive(row, resolver=None) -> Decision:
    """The policy decision for one notice row (a mapping with state,
    location, fields_json).  Quality-evidence rows are not decided here."""
    state = (row.get("state") or "").upper()
    raw = raw_fields(row)
    if state == "IL":
        return _illinois(raw)
    if state == "NY":
        return _new_york(row, raw)
    if state == "GA":
        return _georgia(raw)
    if state == "SC":
        return _south_carolina(raw)
    if state == "PA":
        return _pennsylvania(raw, resolver)
    if state == "FL":
        decision = _florida(raw)
        if decision is not None:
            return decision
    fields = UNLABELED_ADDRESS_FIELDS.get(state)
    if fields and isinstance(raw, dict):
        for field in fields:
            value = str(raw.get(field) or "")
            if re.search(r"(?:^|\n)\s*\d{1,6}[\w./-]*\s+[A-Za-z]", value):
                return Decision(None, None, "not_surfaced_unlabeled_role", field)
    return Decision(None, None, "no_policy_field")


def _quality(row) -> dict:
    details = _load(row.get("source_details"))
    quality = details.get("quality_evidence") if isinstance(details, dict) else None
    return quality if isinstance(quality, dict) else {}


def basis(row) -> str | None:
    """site_address_basis for an exported row.

    Needs only state, site_address and source_details, so the CSV export and
    the site payload agree.  It relies on the invariant ``apply`` enforces:
    in a surface state, a stored site_address is the policy's own value.
    """
    address = row.get("site_address")
    if not address:
        return None
    quality = _quality(row)
    sites = [site for site in quality.get("sites") or [] if isinstance(site, dict)]
    if any(site.get("address") == address for site in sites):
        return BASIS_QUALITY
    state = (row.get("state") or "").upper()
    return SURFACE_STATES.get(state, BASIS_UNVERIFIED)


def apply(conn, *, states: set[str] | None = None, resolver=None,
          dry_run: bool = False) -> dict:
    """Recompute site_address for every notice outside quality evidence.

    For each notice in ``states`` (default: every state), a row annotated by
    the quality-evidence pass, or in a quality-evidence state, is left to
    that pass.  Every other row gets the policy's address or NULL.  Returns
    per-state counts of surfaced addresses and of each rejection reason.
    """
    if resolver is None and (states is None or "PA" in states):
        from warnlive.enrich.places import Resolver

        resolver = Resolver()
    where, params = "", ()
    if states is not None:
        selected = sorted({s.upper() for s in states})
        if not selected:
            return {"rule": RULE, "states": {}, "changed": 0}
        where = f"WHERE n.state IN ({','.join('?' for _ in selected)})"
        params = tuple(selected)
    rows = conn.execute(
        "SELECT n.id, n.state, n.location, n.site_address, n.source_details, "
        "v.fields_json FROM notices n JOIN notice_versions v "
        "ON v.notice_id = n.id AND v.version = n.current_version "
        f"{where} ORDER BY n.id", params,
    ).fetchall()
    per_state: dict[str, Counter] = defaultdict(Counter)
    updates: list[tuple[str | None, int]] = []
    for sqlrow in rows:
        row = dict(sqlrow)
        state = row["state"]
        counts = per_state[state]
        if state in QUALITY_STATES or _quality(row):
            counts["left_to_quality_evidence"] += 1
            continue
        decision = derive(row, resolver)
        if decision.address:
            counts["surfaced"] += 1
            if decision.reason == "accepted_after_name_prefix":
                counts["surfaced_after_name_prefix"] += 1
        elif decision.reason not in {"no_policy_field", "no_labeled_site_field",
                                     "no_address_field", "no_address_in_field"}:
            counts[f"rejected:{decision.reason}"] += 1
        if row["site_address"] != decision.address:
            counts["changed"] += 1
            updates.append((decision.address, row["id"]))
    if updates and not dry_run:
        with conn:
            conn.executemany("UPDATE notices SET site_address = ? WHERE id = ?", updates)
    return {
        "rule": RULE,
        "changed": len(updates),
        "dry_run": dry_run,
        "states": {state: dict(sorted(counts.items()))
                   for state, counts in sorted(per_state.items()) if counts},
    }
