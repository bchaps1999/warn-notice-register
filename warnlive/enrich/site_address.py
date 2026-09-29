"""Deterministic, source-derived worksite addresses for notices.site_address.

Address role policy (site_address_surface_v2)
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

Every accepted value must also look like one street address (a house
number, no P.O. box, no "multiple/various" text, not two concatenated
street addresses) and must not name another state.  An Illinois row whose
labeled ``Location State`` is another state is rejected: it is an
out-of-state location or mailing address, not an Illinois worksite.

Addresses whose role the source does not label, and for which no
independent filed place exists to check them against, are *not* exported:
Florida's company cell (name plus address, from which ``location`` is
itself derived), Idaho's ``Address``/``City`` block, the America's JobLink
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

RULE = "site_address_surface_v2"

# Basis values exported as site_address_basis.
BASIS_QUALITY = "quality_evidence"
BASIS_LABELED = "labeled_site_field"
BASIS_COUNTY = "filed_county_consistent"
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
}
# state -> raw field holding an address whose role the source does not label
# and which has no independent filed place to check it against.  Counted,
# never exported.
UNLABELED_ADDRESS_FIELDS: dict[str, tuple[str, ...]] = {
    "FL": ("Company Name", "COMPANY NAME"),
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


def _checked(value: str, state: str, basis: str, field: str) -> Decision:
    """Common shape checks for any candidate address."""
    text = _text(value)
    if not text:
        return Decision(None, None, "no_address_in_field", field)
    if _several_addresses(str(value)):
        return Decision(None, None, "multiple_addresses_in_field", field)
    cleaned = clean_address(text, state)
    if cleaned is None:
        if _STREETISH.search(text) and not _JUNK.search(text):
            return Decision(None, None, "names_another_state", field)
        return Decision(None, None, "not_a_street_address", field)
    return Decision(cleaned, basis, "accepted", field)


def _illinois(raw: object) -> Decision:
    field = "Location Address"
    if not isinstance(raw, dict) or not _text(raw.get(field)):
        return Decision(None, None, "no_labeled_site_field")
    labeled_state = _text(raw.get("Location State")).upper()
    if labeled_state and labeled_state not in {"IL", "ILLINOIS"}:
        return Decision(None, None, "labeled_location_state_not_il", field)
    # Composed from the labeled components in the listing's own order:
    # "334 W North St Momence, IL 60954-1157".
    value = _text(raw.get(field))
    city = _text(raw.get("Location City"))
    if city:
        value = f"{value} {city}"
    value = f"{value}, {' '.join(p for p in ('IL', _text(raw.get('Location Zipcode'))) if p)}"
    return _checked(value, "IL", BASIS_LABELED, field)


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
