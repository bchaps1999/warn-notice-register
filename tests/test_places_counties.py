"""Explicit counties, multi-site strings, county-only aliases and typo aliases
in the place resolver (warnlive.enrich.places)."""

import csv
import gzip

import pytest

from warnlive.enrich.places import FIELDS, Resolver, fold

ROSTER = [
    # Iowa: Des Moines is also a county; Polk City folds to "polk".
    ("IA", "county", "Polk County", "", "19153", "Polk County", ""),
    ("IA", "county", "Des Moines County", "", "19057", "Des Moines County", ""),
    ("IA", "county", "Clayton County", "", "19043", "Clayton County", ""),
    ("IA", "county", "Monona County", "", "19133", "Monona County", ""),
    ("IA", "county", "Blount County", "", "19998", "Blount County", ""),
    ("IA", "county", "Sevier County", "", "19999", "Sevier County", ""),
    ("IA", "place", "Des Moines city", "1921000", "19153", "Polk County", "1"),
    ("IA", "place", "Polk City city", "1963975", "19153", "Polk County", "1"),
    ("IA", "place", "Monona city", "1953355", "19043", "Clayton County", "1"),
    ("IA", "place", "Riverside city", "1967000", "19153", "Polk County", "1"),
    # Alaska: Anchorage is a place and a county equivalent.
    ("AK", "county", "Anchorage Municipality", "", "02020", "Anchorage Municipality", ""),
    ("AK", "county", "North Slope Borough", "", "02185", "North Slope Borough", ""),
    ("AK", "place", "Anchorage municipality", "0203000", "02020", "Anchorage Municipality", "1"),
    ("AK", "place", "Prudhoe Bay CDP", "0262430", "02185", "North Slope Borough", ""),
    # Ohio: "City/County" is one site; two pairs are two sites.
    ("OH", "county", "Delaware County", "", "39041", "Delaware County", ""),
    ("OH", "county", "Franklin County", "", "39049", "Franklin County", ""),
    ("OH", "county", "Montgomery County", "", "39113", "Montgomery County", ""),
    ("OH", "place", "Westerville city", "3983342", "39049", "Franklin County", "1"),
    ("OH", "place", "Delaware city", "3921434", "39041", "Delaware County", "1"),
    ("OH", "place", "Worthington city", "3986604", "39049", "Franklin County", "1"),
    ("OH", "place", "Franklin city", "3928826", "39165", "Warren County", "1"),
    ("OH", "place", "Montgomery city", "3951800", "39061", "Hamilton County", "1"),
    # California: a county-only alias and a misspelling.
    ("CA", "county", "Los Angeles County", "", "06037", "Los Angeles County", ""),
    ("CA", "county", "Orange County", "", "06059", "Orange County", ""),
    ("CA", "place", "Pomona city", "0658072", "06037", "Los Angeles County", "1"),
    ("CA", "place", "Carson city", "0611530", "06037", "Los Angeles County", "1"),
    ("CA", "place", "Irvine city", "0636770", "06059", "Orange County", "1"),
]


@pytest.fixture
def resolver(tmp_path):
    roster = tmp_path / "places.csv.gz"
    with gzip.open(roster, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for state, kind, name, place, county_fips, county, inc in ROSTER:
            writer.writerow({"state": state, "kind": kind, "key": fold(name), "name": name,
                             "place_fips": place, "county_fips": county_fips,
                             "county_name": county, "lat": "1", "lon": "2",
                             "incorporated": inc})
    header = ["state", "alias", "place_name", "county_name", "point", "zip3", "note"]
    aliases = tmp_path / "aliases.csv"
    with open(aliases, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerow(["CA", "Rancho Dominguez", "", "Los Angeles County", "county", "", ""])
        writer.writerow(["CA", "El Toro", "", "Orange County", "county", "", ""])
        # A county-only alias whose county is missing is ignored.
        writer.writerow(["CA", "Nowhere Flats", "", "Imaginary County", "county", "", ""])
    typos = tmp_path / "typos.csv"
    with open(typos, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerow(["CA", "Ponoma", "Pomona city", "Los Angeles County", "place", "917", ""])
        # A typo row must name a place.
        writer.writerow(["CA", "Orang", "", "Orange County", "county", "", ""])
    return Resolver(path=roster, alias_path=aliases, typo_path=typos)


def test_city_with_its_county_resolves_the_city(resolver):
    got = resolver.resolve("IA", "Des Moines, Polk County")
    assert (got["place_name"], got["county_fips"], got["geo_basis"]) == (
        "Des Moines city", "19153", "place+county")
    got = resolver.resolve("IA", "Monona, Clayton County")
    assert (got["place_name"], got["county_fips"]) == ("Monona city", "19043")


def test_a_segment_saying_county_is_the_county_not_a_like_named_place(resolver):
    got = resolver.resolve("IA", "Polk County")
    assert (got["place_fips"], got["county_fips"], got["geo_basis"]) == (None, "19153", "county")
    # Several counties named: none is established.
    assert resolver.resolve("IA", "Blount, Des Moines County and Sevier County")["geo_basis"] is None
    # A street with "County" in it is not a county segment.
    got = resolver.resolve("IA", "9994 County Farm Rd Riverside IA 50001")
    assert got["place_name"] == "Riverside city"


def test_list_separated_second_place_is_not_the_first_places_county(resolver):
    assert resolver.resolve("AK", "Prudhoe Bay/ Anchorage")["geo_basis"] is None
    assert resolver.refusals[("AK", "Prudhoe Bay/ Anchorage")] == "two places named"
    assert resolver.resolve(
        "OH", "Worthington/Franklin Westerville/Montgomery")["geo_basis"] is None


def test_ohio_city_slash_county_pair_is_one_site(resolver):
    got = resolver.resolve("OH", "Westerville/Delaware")
    assert (got["place_name"], got["county_fips"], got["geo_basis"]) == (
        "Westerville city", "39041", "place+county")


def test_county_only_alias_places_the_county_and_no_place(resolver):
    got = resolver.resolve("CA", "RANCHO DOMINGUEZ")
    assert (got["place_fips"], got["county_fips"], got["geo_basis"]) == (
        None, "06037", "county_alias")
    assert resolver.resolve("CA", "Nowhere Flats")["geo_basis"] is None
    # With a real place in another county, the filing names two sites.
    assert resolver.resolve("CA", "El Toro / Carson")["geo_basis"] is None
    # With a place in the same county, the county holds.
    assert resolver.resolve("CA", "Rancho Dominguez / Carson")["geo_basis"] == "county"
    # A county filed in the state's own column outranks the alias.
    got = resolver.resolve("CA", "Rancho Dominguez", {"raw_extra": {"county": "Los Angeles"}})
    assert got["geo_basis"] == "county:filed"


def test_typo_alias_is_visible_and_must_name_a_place(resolver):
    got = resolver.resolve("CA", "PONOMA")
    assert (got["place_name"], got["geo_basis"]) == ("Pomona city", "place_typo")
    assert resolver.resolve("CA", "Orang")["geo_basis"] is None
    # The ZIP veto applies as for community aliases.
    assert resolver.resolve("CA", "Ponoma, CA 95814")["geo_basis"] is None
