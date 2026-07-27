"""Where a notice's layoff actually happened, resolved to a real place.

States publish a location as free text and disagree completely about what
belongs in it: a bare city ("FREMONT"), a city and its county
("Cincinnati (Hamilton)"), a street address ("1900 NORTH AUSTIN AVENUE
CHICAGO, IL 60639-5079"), several sites at once ("New York/King/Queens"),
or — in Kansas, Vermont, Maine and Oklahoma — a workforce investment area,
which is not a place at all.

This resolves those strings against the Census place and county rosters, so
a notice carries a county FIPS code that joins to every other federal
dataset rather than a string that joins to nothing.

Geography belongs to the notice, not to the employer, so this is a sibling
of the identity tiers in annotate.py rather than part of them. Like them it
is derived at export time from a reference file: the database keeps only
what a state published, and a better resolver improves every export without
touching a stored row.

The matching rule is the one the identity matcher uses, for the same
reason: a missing place costs only enrichment, a wrong one poisons every
join built on it. So a name that could be two places in the state is
refused rather than resolved to the larger one, matching never crosses a
state line, and what could not be resolved is written to a review file
instead of quietly disappearing. Where a state files the county alongside
the city, that county settles names that would otherwise be ambiguous —
which is why Ohio's "Cincinnati (Hamilton)" is easier to place than
California's bare "Fremont".
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import re
import urllib.request
import zipfile
from pathlib import Path

from warnlive.normalize.engine import filed_address

logger = logging.getLogger("warnlive")

PATH = Path("data/reference/places.csv.gz")
ALIAS_PATH = Path("data/reference/place_aliases.csv")
REVIEW_PATH = Path("data/health/places_review.csv")
FIELDS = [
    "state", "kind", "key", "name",
    "place_fips", "county_fips", "county_name", "lat", "lon", "incorporated",
]
# What resolve() contributes to an exported notice.
RESULT_FIELDS = [
    "place_name", "place_fips", "county_name", "county_fips",
    "latitude", "longitude", "geo_basis",
]

CODES_URL = "https://www2.census.gov/geo/docs/reference/codes2020"
GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer"
KML_URL = "https://www2.census.gov/geo/tiger/GENZ2023/kml"
MULTI_COUNTY = "~~~"  # how the Census joins the counties of a place that spans several

# Legal-status words the Census appends to a name but nobody files with:
# "Abbeville city", "Franklin County", "Anchorage municipality".
_STATUS = re.compile(
    # "zona urbana" only as the whole Puerto Rican phrase — on its own,
    # "urbana" is the name of cities in Illinois, Ohio and Iowa, and
    # stripping it deleted every one of them from the roster.
    r"\b(?:city and borough|census area|city|town|village|borough|township|"
    r"municipality|county|parish|cdp|comunidad|zona urbana|"
    r"metro government|metropolitan government|unified government|"
    r"consolidated government|balance)\b",
    re.IGNORECASE,
)
_PARENTHETICAL = re.compile(r"\([^)]*\)")
_ABBREV = {
    "ft": "fort", "st": "saint", "ste": "sainte", "mt": "mount", "pt": "point",
    "n": "north", "s": "south", "e": "east", "w": "west",  # "N. Richland Hills"
}
_STOPWORDS = {"of", "the"}  # "City of Industry" is the Census's "Industry city"
# A city that merged with its county is filed under its old short name:
# "Nashville-Davidson metropolitan government" is filed as "Nashville".
_CONSOLIDATED = re.compile(
    r"\b(?:consolidated|metro|metropolitan|unified)\s+government\b", re.IGNORECASE
)
_ZIP = re.compile(r"\b\d{5}(?:-\d{4})?\b")
_STREET = re.compile(
    r"^\s*\d+[\w-]*\s+.*?\b(?:street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|"
    r"way|parkway|pkwy|highway|hwy|lane|ln|court|ct|place|pl|circle|cir|"
    r"terrace|ter|trail|trl|route|rte)\b\.?",
    re.IGNORECASE,
)
# The trailing \b matters: without it "fl" matches the front of "Flower" and
# eats the rest, turning Flower Mound into Mound. The designator that follows
# is taken only when it looks like one — a number, a # or a lone letter —
# because "70th Floor Chicago" and "3rd Floor San Francisco" put the city
# immediately after the unit word, and a greedy token would swallow it.
_UNIT = re.compile(
    r"\b(?:suite|ste|unit|floor|fl|building|bldg|apt|#)\b\.?"
    r"(?:\s*(?:[#\d][\w-]*|[a-z]\b))?",
    re.IGNORECASE,
)
_SEGMENT = re.compile(r"[/,;()]|\s+-\s+|\s+&\s+|\s+\band\b\s+")
# Looks like a street address: a house number, or a street-type word. Used
# only to decide whether a segment may be mined for a trailing city name.
#
# "st" is deliberately absent where "street" is present: abbreviated, it is
# far more often Saint. Georgia files "St. Mountain" for Stone Mountain, and
# reading that as a street address mined a city called Mountain out of it. A
# real numbered address is caught by the house-number branch regardless.
_ADDRESS = re.compile(
    r"^\s*\d+[\w-]*\s+\S|\b(?:street|avenue|ave|road|rd|boulevard|blvd|drive|dr|"
    r"way|parkway|pkwy|highway|hwy|lane|ln|court|ct|place|pl|circle|cir|"
    r"terrace|ter|trail|trl|route|rte)\b\.?",
    re.IGNORECASE,
)
# No US place name runs longer than this, and a longer tail would start
# swallowing the street.
_ADDRESS_TAIL_WORDS = 4
# House numbers and compass points are not street names. A tail is only a
# city if something that could be a street is still standing in front of it.
_DIRECTIONAL = {
    "n", "s", "e", "w", "ne", "nw", "se", "sw",
    "north", "south", "east", "west",
}


def _streetish(word: str) -> bool:
    """Whether a word could be part of a street name."""
    bare = word.strip(".").lower()
    return bool(bare) and not bare.isdigit() and bare not in _DIRECTIONAL


def _trailing_noise(word: str) -> bool:
    """Whether a trailing word is a state abbreviation or a stray number."""
    bare = word.strip(".")
    return bare.upper() in _POSTAL or bare.isdigit()


# What states call the columns they publish a city and county in.
_FILED_CITY = ("city", "city_name", "city/town", "worksite city")
_FILED_COUNTY = ("county", "county_name", "county name")


def _filed_place(fields_json: str | dict | None) -> tuple[str, str]:
    """The city and county a state published in their own columns.

    Returned apart rather than glued into a string, because which column a
    value came from is information the string form throws away. fold() strips
    legal-status words, so "Hamilton County" and "Hamilton city" reduce to the
    same key and a county filed as a county would come back as the city of
    that name in a different county. A state saying "county" is worth
    believing.
    """
    if not fields_json:
        return ("", "")
    raw = fields_json
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return ("", "")
    if not isinstance(raw, dict):
        return ("", "")
    extra = raw.get("raw_extra", raw)
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, ValueError):
            return ("", "")
    if not isinstance(extra, dict):
        return ("", "")

    lowered = {
        (k or "").strip().lower(): v.strip()
        for k, v in extra.items()
        if isinstance(v, str) and v.strip()
    }
    return (
        next((lowered[k] for k in _FILED_CITY if k in lowered), ""),
        next((lowered[k] for k in _FILED_COUNTY if k in lowered), ""),
    )


def _foreign_state(state: str, location: str) -> str | None:
    """The other state this location names, if it names one.

    Two letters are a state only where an address puts one: at the very end
    of the string, or in front of a ZIP. Everywhere else they are likelier to
    be a word — "IN" and "OR" are Indiana and Oregon exactly as often as they
    are English, and a street type is worse still. Georgia files "5531 Rafe
    Ct, Flowery Branch, Georgia", where Ct is Court and reading it as
    Connecticut would refuse a Georgia address for naming Georgia.
    """
    segments = [s for s in _SEGMENT.split(location) if (s or "").strip()]
    for index, segment in enumerate(segments):
        has_zip = bool(_ZIP.search(segment))
        if not has_zip and index != len(segments) - 1:
            continue
        words = _ZIP.sub(" ", segment).split()
        while words and words[-1].strip(".").isdigit():
            words.pop()
        if words:
            trailing = words[-1].upper().replace(".", "")
            if trailing in _POSTAL and trailing != state.upper():
                return trailing
    return None
_POSTAL = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO",
    "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA",
    "PR", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}


def fold(name: str) -> str:
    """A place name reduced to what two spellings of it have in common.

    Legal status words go, because a state writes "Cincinnati" where the
    Census writes "Cincinnati city"; so do abbreviations people expand
    inconsistently ("Ft. Worth" and "Fort Worth" are one city).
    """
    text = _PARENTHETICAL.sub(" ", (name or "").lower())
    text = _STATUS.sub(" ", text)
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    words = [_ABBREV.get(w, w) for w in text.split() if w not in _STOPWORDS]
    return "".join(words)


def _keys_for(census_name: str) -> list[str]:
    """Every folded name a Census place might reasonably be filed under.

    A consolidated city-county is filed under the city's old short name, so
    "Louisville/Jefferson County metro government" is indexed as Louisville
    as well as in full. The rule is deliberately confined to those names —
    splitting every hyphen would index Winston-Salem as Winston.
    """
    keys = [fold(census_name)]
    if _CONSOLIDATED.search(census_name):
        short = re.split(r"[-/]", _PARENTHETICAL.sub("", census_name))[0]
        if fold(short) and fold(short) not in keys:
            keys.append(fold(short))
    return [k for k in keys if k]


def _download(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "warn-notice-register/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            raw = archive.read(archive.namelist()[0])
    return raw.decode("latin-1")


def _points(url: str) -> dict[str, tuple[str, str]]:
    """GEOID -> (lat, lon) from a Gazetteer file.

    The Census pads its last header field out with spaces, so the column
    names are stripped before they become keys.
    """
    lines = _download(url).splitlines()
    header = [h.strip() for h in lines[0].split("\t")]
    rows = csv.DictReader(lines[1:], fieldnames=header, delimiter="\t")
    return {
        r["GEOID"].strip(): (r["INTPTLAT"].strip(), (r["INTPTLONG"] or "").strip())
        for r in rows
        if r.get("GEOID")
    }


def _county_rings(url: str) -> dict[str, list[list[tuple[float, float]]]]:
    """county FIPS -> its outer boundary rings, from the Census KML.

    Parsed with iterparse and cleared as it goes: the file is 8 MB of XML
    and only the rings are wanted.
    """
    import xml.etree.ElementTree as ET

    req = urllib.request.Request(url, headers={"User-Agent": "warn-notice-register/1.0"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".kml"))
        payload = archive.read(name)

    geoid_in = re.compile(r"<th>GEOID</th>\s*<td>(\d{5})</td>")
    rings: dict[str, list[list[tuple[float, float]]]] = {}
    for _, element in ET.iterparse(io.BytesIO(payload), events=("end",)):
        if not element.tag.endswith("Placemark"):
            continue
        description = "".join(element.itertext())
        found = geoid_in.search(description)
        if found:
            shapes = []
            for coords in element.iter():
                if not coords.tag.endswith("coordinates") or not coords.text:
                    continue
                ring = []
                for point in coords.text.split():
                    lon, lat, *_ = point.split(",")
                    ring.append((float(lon), float(lat)))
                if len(ring) > 3:
                    shapes.append(ring)
            if shapes:
                rings[found.group(1)] = shapes
        element.clear()
    return rings


def _contains(ring: list[tuple[float, float]], lon: float, lat: float) -> bool:
    """Ray casting: does this ring enclose the point?"""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) / (y2 - y1) * (x2 - x1)
            if crossing > lon:
                inside = not inside
    return inside


def refresh(out_path: Path = PATH) -> int:
    """Rebuild the roster from the Census.

    Four public-domain files: the 2020 code lists, which alone carry the
    county each place sits in, and the 2024 Gazetteer, which alone carries
    each one's interior point.
    """
    county_points = _points(f"{GAZ_URL}/2024_Gaz_counties_national.zip")
    place_points = _points(f"{GAZ_URL}/2024_Gaz_place_national.zip")

    rows: list[dict] = []
    fips_by_county: dict[tuple[str, str], str] = {}
    for r in csv.DictReader(
        _download(f"{CODES_URL}/national_county2020.txt").splitlines(), delimiter="|"
    ):
        geoid = r["STATEFP"] + r["COUNTYFP"]
        fips_by_county[(r["STATE"], r["COUNTYNAME"])] = geoid
        lat, lon = county_points.get(geoid, ("", ""))
        rows.append({
            "state": r["STATE"], "kind": "county", "key": fold(r["COUNTYNAME"]),
            "name": r["COUNTYNAME"], "place_fips": "", "county_fips": geoid,
            "county_name": r["COUNTYNAME"], "lat": lat, "lon": lon,
            "incorporated": "",
        })

    # Chicago, Atlanta and San Antonio each straddle several counties, so the
    # roster alone cannot say which one a notice belongs to — and picking the
    # first would file Atlanta's layoffs under whichever county sorts first.
    # The place's own interior point settles it: whichever of its counties
    # actually contains that point is the county the place sits in.
    try:
        rings = _county_rings(f"{KML_URL}/cb_2023_us_county_500k.zip")
    except Exception as exc:  # noqa: BLE001 — degrade to no county, never crash
        logger.warning("places: county boundaries unavailable (%s)", exc)
        rings = {}

    straddling: list[str] = []
    for r in csv.DictReader(
        _download(f"{CODES_URL}/national_place2020.txt").splitlines(), delimiter="|"
    ):
        geoid = r["STATEFP"] + r["PLACEFP"]
        counties = [c for c in (r["COUNTIES"] or "").split(MULTI_COUNTY) if c]
        lat, lon = place_points.get(geoid, ("", ""))
        county_name = counties[0] if len(counties) == 1 else ""
        if len(counties) > 1 and lat and lon:
            straddling.append(geoid)
            # A city sharing its name with one of its counties belongs to
            # that one: New York city spans five boroughs, and the one it is
            # named for is New York County, not whichever holds its centre.
            county_name = next(
                (c for c in counties if fold(c) == fold(r["PLACENAME"])),
                "",
            ) or next(
                (
                    c for c in counties
                    if any(
                        _contains(ring, float(lon), float(lat))
                        for ring in rings.get(fips_by_county.get((r["STATE"], c), ""), [])
                    )
                ),
                "",
            )
        for key in _keys_for(r["PLACENAME"]):
            rows.append({
                "state": r["STATE"], "kind": "place", "key": key,
                "name": r["PLACENAME"], "place_fips": geoid,
                "county_fips": fips_by_county.get((r["STATE"], county_name), ""),
                "county_name": county_name, "lat": lat, "lon": lon,
                # C-classes are incorporated municipalities, U-classes are
                # census designated places: unincorporated areas the Census
                # names for statistics. Where both share a name, a WARN
                # filing means the municipality.
                "incorporated": "1" if r["CLASSFP"].startswith("C") else "",
            })

    # New Jersey, New York, Connecticut and the New England states file from
    # townships, which are county subdivisions rather than places — Edison NJ
    # is a township, not a Census place. Only functioning governments are
    # taken (FUNCSTAT "A"); the rest are census county divisions, statistical
    # areas drawn for tabulation that nobody files from.
    for r in csv.DictReader(
        _download(f"{CODES_URL}/national_cousub2020.txt").splitlines(), delimiter="|"
    ):
        if r["FUNCSTAT"] != "A":
            continue
        geoid = r["STATEFP"] + r["COUNTYFP"]
        lat, lon = county_points.get(geoid, ("", ""))
        rows.append({
            "state": r["STATE"], "kind": "cousub", "key": fold(r["COUSUBNAME"]),
            "name": r["COUSUBNAME"], "place_fips": "", "county_fips": geoid,
            "county_name": r["COUNTYNAME"], "lat": lat, "lon": lon,
            "incorporated": "",
        })

    rows = [r for r in rows if r["key"]]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["state"], r["kind"], r["key"])):
            writer.writerow(row)
    straddling_placed = sum(
        1 for r in rows
        if r["kind"] == "place" and r["place_fips"] in set(straddling) and r["county_fips"]
    )
    logger.info(
        "places: %d rows (%d counties, %d places); %d of %d places straddling "
        "county lines were placed by their interior point",
        len(rows), sum(1 for r in rows if r["kind"] == "county"),
        sum(1 for r in rows if r["kind"] == "place"),
        straddling_placed, len(straddling),
    )
    return len(rows)


def load_aliases(path: Path = ALIAS_PATH) -> dict[tuple[str, str], tuple[str, str]]:
    """(state, folded filed name) -> (folded Census name, kind), by hand.

    The kind pins what the alias means. A neighbourhood that is part of an
    incorporated city resolves to that city, but an unincorporated community
    like Universal City is in no city at all, and can only honestly be placed
    in its county — so its alias says "county" and the place is left empty.
    """
    if not path.exists():
        return {}
    with open(path, newline="") as fh:
        return {
            (r["state"].upper(), fold(r["filed_name"])): (
                fold(r["census_name"]), (r.get("kind") or "").strip(),
            )
            for r in csv.DictReader(fh)
            if r.get("state") and r.get("filed_name") and r.get("census_name")
            and (r.get("decision") or "").strip().lower() != "reject"
        }


def load_rejections(path: Path = ALIAS_PATH) -> set[tuple[str, str]]:
    """(state, folded location) strings settled as naming no place at all.

    The review file is rebuilt from the database on every refresh, so a
    string nobody can place returns to the top of it forever unless the fact
    that it was examined is written down. Kansas and Oklahoma file against
    workforce investment areas, and "Various Cities/Various Counties" names
    no county in particular; these are not failures to be retried, they are
    answers. A rejection grants no geography — it only stops the asking.
    """
    if not path.exists():
        return set()
    with open(path, newline="") as fh:
        return {
            (r["state"].upper(), fold(r["filed_name"]))
            for r in csv.DictReader(fh)
            if r.get("state") and r.get("filed_name")
            and (r.get("decision") or "").strip().lower() == "reject"
        }


def review(conn, out_path: Path = REVIEW_PATH,
           alias_path: Path = ALIAS_PATH) -> int:
    """Write the location strings that resolved to nothing, worst first.

    A refusal is only defensible if somebody can see it. Most of these are
    correct — Kansas and Maine file against workforce investment areas, which
    are not places and never will be — but the rest are the working list for
    data/reference/place_aliases.csv, ranked by the workers riding on them.
    """
    resolver = Resolver(alias_path=alias_path)
    rejected = load_rejections(alias_path)
    unresolved: dict[tuple[str, str], dict] = {}
    for row in conn.execute(
        "SELECT n.state AS state, n.location AS location, "
        "       n.employer_name AS employer_name, "
        "       COALESCE(n.employees_affected, 0) AS jobs, "
        "       (SELECT v.fields_json FROM notice_versions v "
        "        WHERE v.notice_id = n.id AND v.version = n.current_version"
        "       ) AS fields_json FROM notices n "
        "WHERE n.location IS NOT NULL AND n.location != ''"
    ):
        if resolver.resolve(
            row["state"], row["location"], row["fields_json"], row["employer_name"]
        )["geo_basis"]:
            continue
        if ((row["state"] or "").upper(), fold(row["location"])) in rejected:
            continue
        key = (row["state"], row["location"])
        entry = unresolved.setdefault(key, {"notices": 0, "workers": 0})
        entry["notices"] += 1
        entry["workers"] += row["jobs"]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["state", "location", "notices", "workers", "reason"]
        )
        writer.writeheader()
        for (state, location), entry in sorted(
            unresolved.items(), key=lambda kv: (-kv[1]["workers"], kv[0])
        ):
            writer.writerow({
                "state": state, "location": location, **entry,
                "reason": resolver.refusals.get((state, location), ""),
            })
    logger.info("places review: %d unresolved locations -> %s", len(unresolved), out_path)
    return len(unresolved)


class Resolver:
    """Resolves a filed location string to a Census place and county."""

    def __init__(self, path: Path = PATH, alias_path: Path = ALIAS_PATH) -> None:
        self.places: dict[tuple[str, str], list[dict]] = {}
        self.counties: dict[tuple[str, str], list[dict]] = {}
        self.subdivisions: dict[tuple[str, str], list[dict]] = {}
        tables = {
            "place": self.places, "county": self.counties, "cousub": self.subdivisions,
        }
        if path.exists():
            with gzip.open(path, "rt") as fh:
                for row in csv.DictReader(fh):
                    table = tables.get(row["kind"])
                    if table is not None:
                        table.setdefault((row["state"], row["key"]), []).append(row)
        self.aliases = load_aliases(alias_path)
        self._cache: dict[tuple[str, str], dict] = {}
        # Why each unresolved string failed, for the review file.
        self.refusals: dict[tuple[str, str], str] = {}

    def _keys(self, state: str, location: str) -> list[tuple[str, str]]:
        """The candidate (folded name, kind) pairs inside a location string."""
        # An alias may name the whole filed string rather than a name inside
        # it. Segmenting finds a place only when the string contains one:
        # "O'HARE INTERNATIONAL AIRPORT CHICAGO, IL 60666" folds to a single
        # segment that is no place at all, and no rule will ever make it one.
        # Naming the string outright is the only way to say where it is, and
        # it settles the whole string rather than a word that might recur
        # innocently elsewhere.
        whole = self.aliases.get((state, fold(location)))
        if whole:
            return [whole]
        keys = []
        for segment in _SEGMENT.split(location):
            segment = _UNIT.sub(" ", segment or "")
            # "1900 North Austin Avenue Chicago" — the city follows the street.
            # The address goes before the ZIP does: a five-digit house number
            # ("13800 Gentilly Road") is indistinguishable from a ZIP code, so
            # stripping ZIPs first would leave a street with no number and the
            # address stripper would no longer recognise it.
            segment = _STREET.sub(" ", segment)
            segment = _ZIP.sub(" ", segment)
            words = segment.split()
            # "Arlington VA", "Washington D.C." — a trailing state
            # abbreviation is not part of the place name.
            while words and words[-1].upper().replace(".", "") in _POSTAL:
                words.pop()
            key = fold(" ".join(words))
            if key:
                keys.append(self.aliases.get((state, key), (key, "")))
        return keys

    def _address_tails(self, state: str, location: str) -> list[str]:
        """Trailing place names to try when a street address hides its city.

        A US address ends "<street> <City>, <ST> <ZIP>", and the street is
        recognisable only when it carries a type word — Illinois writes
        "2200 E. Eldorado Decatur, IL 62521" with none, so nothing marks
        where Eldorado stops and Decatur starts.

        The city's position does, though: it is what sits at the end. So the
        trailing words are tried as a name, longest first, and the roster
        decides. This runs only after the ordinary reading has failed, and
        only on segments that look like an address, so a phrase like "various
        locations in Washington" is never mined for a place name it does not
        claim to be.
        """
        if not any(_ADDRESS.search(seg or "") for seg in _SEGMENT.split(location)):
            return []

        # Mined from the whole string rather than segment by segment. A comma
        # splits an address into fragments — "ROUTE 148 NORTH, BOX 566 SESSER,
        # IL 62884" — and the city ends the address, not the first fragment
        # that happens to look like one.
        words = _ZIP.sub(" ", _UNIT.sub(" ", location)).replace(",", " ").split()
        # A state abbreviation and whatever trails it: no US place is a bare
        # number, so a stray digit — half a ZIP, a site count, "MD 21240 1" —
        # is noise, and leaving it would stop the state coming off and take
        # the city down with it.
        while words and _trailing_noise(words[-1]):
            words.pop()

        tails: list[str] = []
        # Longest first: "West Des Moines" before "Des Moines".
        for size in range(min(_ADDRESS_TAIL_WORDS, len(words) - 1), 0, -1):
            # Something recognisable as a street has to remain in front of the
            # tail. Without this a bare street line donates its own name —
            # "1234 Burbank" and "2200 E. Eldorado" are addresses missing
            # their city, not places called Burbank and Eldorado.
            if not any(_streetish(w) for w in words[:-size]):
                continue
            key = fold(" ".join(words[-size:]))
            if key and key not in tails:
                tails.append(key)
        return tails

    def resolve(self, state: str, location: str | None,
                fields_json: str | dict | None = None,
                employer_name: str | None = None) -> dict:
        """Place and county for one notice; every RESULT_FIELDS key is present.

        The filed location string is read first, because it names the site
        the notice is actually about. Where it yields nothing, the components
        the state published in their own columns are tried — and they are
        often decisive, because a string a parser cannot crack is frequently
        accompanied by a plain county field. Illinois files "O'HARE
        INTERNATIONAL AIRPORT CHICAGO" and a county of Cook; Texas files "DFW
        Airport" and a county of Tarrant, settling by hand a question that
        straddles two counties and that no amount of reasoning about the
        string could answer.

        A basis resolved this way is marked ":filed", so a consumer can tell
        a county the state stated from one this pipeline inferred.
        """
        state = (state or "").upper()
        if not state:
            return dict.fromkeys(RESULT_FIELDS)
        out = dict.fromkeys(RESULT_FIELDS)
        if location:
            cached = self._cache.get((state, location))
            if cached is None:
                cached = self._resolve(state, location)
                self._cache[(state, location)] = cached
            out = dict(cached)
            if out["geo_basis"]:
                return out

        city, county = _filed_place(fields_json)
        # City with its county first: that is the shape the resolver reads
        # best, and the county settles a city name repeating within a state.
        for candidate in ([f"{city} ({county})"] if city and county else []) + (
            [city] if city else []
        ):
            got = self.resolve(state, candidate)
            if got["geo_basis"]:
                got["geo_basis"] = f"{got['geo_basis']}:filed"
                return got
        if county:
            got = self._county_only(state, county)
            if got:
                return got

        # Last: the address some states append to the employer name.
        #
        # Florida does this on half its notices and leaves the location
        # column empty, so "Staples 2305 S.W. 32nd Avenue, Bldg. L Pembroke
        # Park, FL 33023" is the only record that the layoff was in Pembroke
        # Park. Read last because it is the least deliberate of the three: a
        # location column and a county field were filled in as such, while
        # this is a site that happened to be typed into the wrong box.
        address = filed_address(employer_name)
        if address:
            got = self.resolve(state, address)
            if got["geo_basis"]:
                got["geo_basis"] = f"{got['geo_basis']}:name"
                return got
        return out

    def _county_only(self, state: str, county: str) -> dict | None:
        """A county looked up as a county, because a state said it was one."""
        rows = self.counties.get((state, fold(county)), [])
        if len(rows) != 1:
            return None
        row = rows[0]
        out = dict.fromkeys(RESULT_FIELDS)
        out.update(
            county_name=row["county_name"], county_fips=row["county_fips"],
            latitude=row["lat"] or None, longitude=row["lon"] or None,
            geo_basis="county:filed",
        )
        return out

    def _resolve(self, state: str, location: str) -> dict:
        out = dict.fromkeys(RESULT_FIELDS)

        # A string that names its own state, and names another one, describes
        # somewhere this notice's roster cannot reach. Illinois files "2323
        # KENNEDY DRIVE JANESVILLE, WI 53547", and Illinois has a Janesville
        # of its own — resolving against the filing state would put the layoff
        # in the wrong one, silently and with full confidence. Refusing is the
        # same trade the rest of the module makes: a missing place costs
        # enrichment, a wrong one poisons every join built on it.
        foreign = _foreign_state(state, location)
        if foreign:
            self.refusals[(state, location)] = f"address is in {foreign}, not {state}"
            return out

        keys = self._keys(state, location)
        if not keys:
            self.refusals[(state, location)] = "no place name in the string"
            return out

        # A county named alongside the city is the strongest signal in the
        # string: it settles a city name that repeats within the state.
        counties = {
            rows[0]["county_fips"]: rows[0]
            for key, _ in keys
            for rows in [self.counties.get((state, key), [])]
            if len(rows) == 1
        }
        county = next(iter(counties.values())) if len(counties) == 1 else None

        # A segment that named the county is context, not a rival place.
        # Ohio files "Cincinnati (Hamilton)" and Ohio also has a city called
        # Hamilton, so without this the string looks like two places at once.
        county_keys = {
            key for key, _ in keys
            if county and len(self.counties.get((state, key), [])) == 1
        }

        found: dict[str, dict] = {}
        place_keys: set[str] = set()
        ambiguous = False
        agreed: dict | None = None  # rival place records that share a county
        for key, kind in keys:
            if kind == "county" or (key in county_keys and len(keys) > 1):
                continue
            candidates = self.places.get((state, key), [])
            if county and len(candidates) > 1:
                narrowed = [
                    c for c in candidates
                    if c["county_fips"] in ("", county["county_fips"])
                ]
                candidates = narrowed or candidates
            if len(candidates) > 1:
                # California has both a Burbank city and a Burbank CDP. An
                # employer files from a municipality, so the incorporated
                # place settles it — but only if exactly one is.
                municipal = [c for c in candidates if c["incorporated"]]
                if len(municipal) == 1:
                    candidates = municipal
            if len(candidates) == 1:
                found[candidates[0]["place_fips"]] = candidates[0]
                place_keys.add(key)
            elif len(candidates) > 1:
                ambiguous = True
                # Rival records can still agree on where they are: Kentucky
                # lists both "Louisville city" and the Louisville/Jefferson
                # metro government, and both sit in Jefferson County. Which
                # record is meant is unknowable; the county is not.
                shared = {c["county_fips"] for c in candidates}
                if len(shared) == 1 and not agreed:
                    agreed = candidates[0]

        if len(found) == 1:
            place = next(iter(found.values()))
            # A county the state named in its own segment outranks the one
            # derived from the place — that is source data against inference.
            # But a *single* segment matching both is a coincidence of names,
            # not a county field: Texas has a Houston County that Houston is
            # not in, a Tyler County that Tyler is not in, and Iowa City is
            # in Johnson County rather than Iowa County.
            filed = county if county and county["key"] not in place_keys else None
            out.update(
                place_name=place["name"], place_fips=place["place_fips"],
                county_name=(filed or {}).get("county_name") or place["county_name"],
                county_fips=(filed or {}).get("county_fips") or place["county_fips"],
                latitude=place["lat"] or None, longitude=place["lon"] or None,
                geo_basis="place+county" if filed else "place",
            )
            return out

        if not found:
            # No place by that name — but the township states file from county
            # subdivisions, so Edison NJ is a township rather than a place.
            # Consulted last: a township often shares a name with a borough
            # nearby, and the place is the better answer where both exist.
            township = next(
                (
                    rows[0] for key, _ in keys
                    for rows in [self.subdivisions.get((state, key), [])]
                    if len(rows) == 1
                ),
                None,
            )
            settled = county or agreed or township
            if settled:
                out.update(
                    county_name=settled["county_name"],
                    county_fips=settled["county_fips"],
                    latitude=settled["lat"] or None, longitude=settled["lon"] or None,
                    geo_basis="subdivision" if settled is township else "county",
                )
                return out

        # Several distinct places in one string is a multi-site filing; the
        # county still holds if they all sit in one, and otherwise the notice
        # is genuinely about more than one place and gets neither.
        if len(found) > 1:
            shared = {p["county_fips"] for p in found.values() if p["county_fips"]}
            if len(shared) == 1 and len(shared) == len(found):
                place = next(iter(found.values()))
                out.update(
                    county_name=place["county_name"], county_fips=place["county_fips"],
                    geo_basis="county",
                )
                return out
            self.refusals[(state, location)] = f"{len(found)} distinct places named"
            return out

        # Last resort: the string may be a street address whose city the
        # ordinary reading could not isolate, because nothing in it marks
        # where the street ends. The city is at the end, so the roster is
        # asked about the trailing words.
        for key in self._address_tails(state, location):
            candidates = self.places.get((state, key), [])
            if len(candidates) > 1:
                municipal = [c for c in candidates if c["incorporated"]]
                candidates = municipal if len(municipal) == 1 else candidates
            if len(candidates) == 1:
                place = candidates[0]
                out.update(
                    place_name=place["name"], place_fips=place["place_fips"],
                    county_name=place["county_name"],
                    county_fips=place["county_fips"],
                    latitude=place["lat"] or None, longitude=place["lon"] or None,
                    geo_basis="address",
                )
                return out

        self.refusals[(state, location)] = (
            "name matches more than one place" if ambiguous else "no matching place"
        )
        return out
