"""Community aliases (data/reference/place_aliases.csv) in the place resolver."""

import csv
import gzip

import pytest

from warnlive.enrich.places import ALIAS_PATH, FIELDS, Resolver, fold

ROSTER = [
    ("CA", "county", "Los Angeles County", "", "06037", "Los Angeles County", "34.2", "-118.3", ""),
    ("CA", "county", "San Diego County", "", "06073", "San Diego County", "33.0", "-116.8", ""),
    ("CA", "place", "Los Angeles city", "0644000", "06037", "Los Angeles County", "34.0", "-118.4", "1"),
    ("CA", "place", "San Diego city", "0666000", "06073", "San Diego County", "32.8", "-117.1", "1"),
    ("CA", "place", "Vista city", "0682996", "06073", "San Diego County", "33.2", "-117.2", "1"),
    ("NY", "county", "Kings County", "", "36047", "Kings County", "40.6", "-73.9", ""),
    ("NY", "county", "Bronx County", "", "36005", "Bronx County", "40.8", "-73.8", ""),
    ("NY", "county", "Queens County", "", "36081", "Queens County", "40.7", "-73.8", ""),
    ("NY", "county", "New York County", "", "36061", "New York County", "40.8", "-73.97", ""),
    ("NY", "county", "Richmond County", "", "36085", "Richmond County", "40.5", "-74.1", ""),
    ("NY", "place", "New York city", "3651000", "36061", "New York County", "40.66", "-73.94", "1"),
    # A real place wins over an alias of the same name.
    ("NY", "place", "Woodside village", "3600001", "36059", "Nassau County", "40.7", "-73.6", "1"),
]
ALIASES = [
    ("CA", "Chatsworth", "Los Angeles city", "Los Angeles County", "place", "913"),
    ("CA", "Playa Vista", "Los Angeles city", "Los Angeles County", "place", "900"),
    ("CA", "La Jolla", "San Diego city", "San Diego County", "place", "920"),
    ("NY", "Brooklyn", "New York city", "Kings County", "county", "112"),
    ("NY", "Bronx", "New York city", "Bronx County", "county", "104"),
    ("NY", "Jamaica", "New York city", "Queens County", "county", "114"),
    ("NY", "Woodside", "New York city", "Queens County", "county", "113"),
    # Target missing from the roster: ignored rather than guessed.
    ("CA", "Valencia", "Santa Clarita city", "Los Angeles County", "place", "913"),
]


@pytest.fixture
def resolver(tmp_path):
    roster = tmp_path / "places.csv.gz"
    with gzip.open(roster, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for state, kind, name, place, county_fips, county, lat, lon, inc in ROSTER:
            writer.writerow({"state": state, "kind": kind, "key": fold(name), "name": name,
                             "place_fips": place, "county_fips": county_fips,
                             "county_name": county, "lat": lat, "lon": lon,
                             "incorporated": inc})
    aliases = tmp_path / "aliases.csv"
    with open(aliases, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["state", "alias", "place_name", "county_name", "point", "zip3", "note"])
        for row in ALIASES:
            writer.writerow([*row, ""])
    return Resolver(path=roster, alias_path=aliases)


def test_la_neighborhood_resolves_to_the_city_with_a_visible_basis(resolver):
    got = resolver.resolve("CA", "CHATSWORTH")
    assert got["place_name"] == "Los Angeles city"
    assert got["county_name"] == "Los Angeles County"
    assert (got["latitude"], got["longitude"]) == ("34.0", "-118.4")
    assert got["geo_basis"] == "place_alias"
    # From the tail of a street address too; before the alias, "Vista"
    # alone matched a San Diego County city.
    tail = resolver.resolve("CA", "12015 E Waterfront Dr. Playa Vista CA 90094")
    assert tail["place_name"] == "Los Angeles city"
    assert tail["geo_basis"] == "place_alias"


def test_borough_gets_its_own_county_and_county_point(resolver):
    got = resolver.resolve("NY", "303 LOUISIANA AVENUE  BROOKLYN, NY, 11207")
    assert got["place_name"] == "New York city"
    assert got["county_name"] == "Kings County"
    assert got["county_fips"] == "36047"
    assert (got["latitude"], got["longitude"]) == ("40.6", "-73.9")
    assert resolver.resolve("NY", "2488 GRAND CONCOURSE  BRONX, NY, 10458")["county_fips"] == "36005"
    assert resolver.resolve("NY", "90-48 160th Street  Jamaica, NY, 11432")["county_fips"] == "36081"


def test_several_boroughs_name_the_city_but_no_county(resolver):
    for location in ("New York/Bronx", "Brooklyn/Bronx", "Kings/Bronx/Richmond"):
        got = resolver.resolve("NY", location)
        assert got["place_name"] == "New York city", location
        assert got["county_fips"] is None, location
        assert got["geo_basis"] == "place_alias", location


def test_aliases_never_shadow_places_or_contradict_a_zip(resolver):
    assert resolver.resolve("NY", "Woodside")["place_name"] == "Woodside village"
    # A ZIP from another part of the state vetoes the alias.
    assert resolver.resolve("NY", "10 Main Street  Jamaica, NY, 12345")["geo_basis"] is None
    # An alias whose target is absent from the roster is ignored.
    assert resolver.resolve("CA", "Valencia")["geo_basis"] is None


def test_shipped_alias_table_targets_exist_in_the_roster():
    """Every alias row must load against the committed Census roster."""
    resolver = Resolver()
    if not resolver.places:
        pytest.skip("places roster not built")
    with open(ALIAS_PATH, newline="") as fh:
        rows = list(csv.DictReader(fh))
    loaded = {(state, alias) for state, alias in resolver.aliases}
    missing = [(r["state"], r["alias"]) for r in rows
               if (r["state"], fold(r["alias"])) not in loaded]
    assert missing == []
