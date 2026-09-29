"""The site_address role policy (warnlive.enrich.site_address)."""

import csv
import gzip
import json

import pytest

from warnlive.enrich import site_address
from warnlive.enrich.notice_quality import project, resolve_geo
from warnlive.enrich.places import FIELDS, Resolver, fold
from warnlive.store import db
from warnlive.store.dedupe import ingest

NY_ROW = ["Acme Corp", "2020-05-01", "2020-03-02", "2020-03-03",
          "303 Louisiana Avenue  Brooklyn, NY, 11207", "Kings", "Closure",
          "Permanent", "Economic", "1", "40", "40"]


def _row(state, raw, location=None, **extra):
    return {"state": state, "location": location,
            "fields_json": json.dumps({"raw_extra": json.dumps(raw)}), **extra}


@pytest.fixture
def resolver(tmp_path):
    rows = [
        ("PA", "county", "Cambria County", "", "42021", "Cambria County", ""),
        ("PA", "county", "Luzerne County", "", "42079", "Luzerne County", ""),
        ("PA", "place", "Johnstown city", "4238288", "42021", "Cambria County", "1"),
        ("PA", "place", "Wilkes-Barre city", "4285152", "42079", "Luzerne County", "1"),
    ]
    path = tmp_path / "places.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for state, kind, name, place, county_fips, county, inc in rows:
            writer.writerow({"state": state, "kind": kind, "key": fold(name), "name": name,
                             "place_fips": place, "county_fips": county_fips,
                             "county_name": county, "lat": "40.3", "lon": "-78.9",
                             "incorporated": inc})
    return Resolver(path=path, alias_path=tmp_path / "none.csv")


def test_illinois_labeled_location_is_composed_from_its_components():
    raw = {"Location Address": "334 W North St ", "Location City": "Momence",
           "Location State": "IL", "Location Zipcode": "60954-1157"}
    got = site_address.derive(_row("IL", raw, "334 W North St Momence, IL 60954-1157"))
    assert got.address == "334 W North St Momence, IL 60954-1157"
    assert got.basis == "labeled_site_field"
    assert got.source_field == "Location Address"


@pytest.mark.parametrize("raw, reason", [
    ({"Location Address": "401 Cottage Street", "Location City": "Abilene",
      "Location State": "KS", "Location Zipcode": "67410"},
     "labeled_location_state_not_il"),
    ({"Location Address": "1020 Olympic Dr. 950 Raddant Rd.", "Location City": "Batavia",
      "Location State": "IL"}, "multiple_addresses_in_field"),
    ({"Location Address": "O'HARE INTERNATIONAL AIRPORT", "Location City": "Chicago",
      "Location State": "IL"}, "not_a_street_address"),
    ({"Location Address": "1700 Harvester Dr, PO Box 2200", "Location City": "West Chicago",
      "Location State": "IL"}, "not_a_street_address"),
])
def test_illinois_out_of_state_and_unsited_values_are_rejected(raw, reason):
    got = site_address.derive(_row("IL", raw))
    assert got.address is None
    assert got.reason == reason


def test_new_york_dashboard_site_cell_is_the_labeled_site():
    got = site_address.derive(_row("NY", NY_ROW, NY_ROW[4]))
    assert got.address == "303 Louisiana Avenue Brooklyn, NY, 11207"
    # Some sources append the Impacted Site County to the listing location.
    appended = site_address.derive(_row("NY", NY_ROW, f"{NY_ROW[4]}, Kings"))
    assert appended.address == got.address
    # A location that did not come from that cell is not vouched for by it.
    other = site_address.derive(_row("NY", NY_ROW, "Albany"))
    assert other.reason == "location_not_from_labeled_field"


def test_georgia_takes_the_first_location_only_for_a_single_site_filing():
    single = {"First Location Address": "2605 Circle 75 Pkwy, Atlanta, Georgia",
              "Second Location Address": "",
              "Number of Locations Associated with WARN Event": "One"}
    got = site_address.derive(_row("GA", single))
    assert got.address == "2605 Circle 75 Pkwy, Atlanta, Georgia"
    several = dict(single, **{"Second Location Address": "10 Main St, Macon, Georgia"})
    assert site_address.derive(_row("GA", several)).reason == "multiple_sites_listed"
    counted = dict(single, **{"Number of Locations Associated with WARN Event": "Multiple"})
    assert site_address.derive(_row("GA", counted)).reason == "multiple_sites_listed"
    # The separate Company Address is the employer's, never a site.
    company = {"Company Address": "7437 Race Rd, Hanover, Maryland"}
    assert site_address.derive(_row("GA", company)).address is None


def test_south_carolina_worksite_column_rejects_a_headquarters_elsewhere():
    ok = site_address.derive(_row("SC", {"address": "101 Michelin Drive, Laurens, SC 29360"}))
    assert ok.address == "101 Michelin Drive, Laurens, SC 29360"
    hq = site_address.derive(_row("SC", {"address": "1990 Wittington Place, Dallas, Texas 75234"}))
    assert hq.reason == "names_another_state"


def test_pennsylvania_address_must_resolve_to_the_filed_county(resolver):
    raw = {"addressfull": "1003 Broad Street, #101\n, Johnstown, PA 15906", "county": "Cambria"}
    got = site_address.derive(_row("PA", raw, "Cambria"), resolver)
    assert got.address == "1003 Broad Street, #101 , Johnstown, PA 15906"
    assert got.basis == "filed_county_consistent"
    wrong = dict(raw, county="Luzerne")
    assert site_address.derive(_row("PA", wrong), resolver).reason == \
        "address_county_differs_from_filed_county"
    two = {"addressfull": "600 Boyce Road, Johnstown, PA 15905\n, 112 Tech Drive, Johnstown, PA 15904",
           "county": "Cambria"}
    assert site_address.derive(_row("PA", two), resolver).reason == "multiple_addresses_in_field"


@pytest.mark.parametrize("value, several", [
    ("72 County Road 53  Greenwich, NY, 12834", False),
    ("1101 Market Street\n,  23rd Floor\n, Philadelphia, PA  19107", False),
    ("2 Park Avenue 10 FL New York, NY, 10016", False),
    ("61 Edson Street and 6 Sam Stratton Road  Amsterdam, NY, 12010", True),
    ("333 E. Water Street & 971 County Road 64  Elmira, NY, 14901", True),
    ("105 N Christopher Ct, 24 Herring Road; and 76 Sprayberry Road, Newnan, Georgia", True),
])
def test_numbered_roads_and_floors_are_not_second_addresses(value, several):
    assert site_address._several_addresses(value) is several


def test_unlabeled_company_address_is_counted_but_not_exported():
    raw = {"Company Name": "Delta Apparel, Inc. \n404 Duval Street\n\nKEY WEST, FL, 33040"}
    got = site_address.derive(_row("FL", raw, "KEY WEST"))
    assert got.address is None
    assert got.reason == "not_surfaced_unlabeled_role"


def _notice(key, state, location, raw):
    from warnlive.normalize.engine import _record_hash

    rec = {"dedupe_key": key, "state": state, "employer_name": "Example",
           "location": location, "notice_date": "2024-04-01",
           "effective_date": "2024-05-31", "employees_affected": 10,
           "layoff_type": "mass_layoff", "is_temporary": 0, "is_amendment": 0,
           "source_url": "https://example.gov", "source_notice_id": key,
           "raw_extra": json.dumps(raw)}
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_apply_is_idempotent_and_leaves_quality_rows_alone(tmp_path):
    conn = db.connect(tmp_path / "t.sqlite")
    db.init_db(conn)
    ingest(conn, [
        _notice("ny", "NY", NY_ROW[4], NY_ROW),
        _notice("fl", "FL", "KEY WEST",
                {"Company Name": "Delta Apparel \n404 Duval Street\nKEY WEST, FL, 33040"}),
        _notice("ca", "CA", "Fremont", {}),
        _notice("ga", "GA", "Macon", {"Company Address": "1 Main St, Macon, Georgia"}),
    ], "2026-09-29")
    # A stale value in a governed state (e.g. from the retired pass) is
    # cleared; a quality-evidence state's value is not this pass's to judge.
    conn.execute("UPDATE notices SET site_address='1 Main St' WHERE dedupe_key='ga'")
    conn.execute("UPDATE notices SET site_address='1 Report Row' WHERE dedupe_key='ca'")
    conn.commit()
    report = site_address.apply(conn, resolver=object())
    got = dict(conn.execute("SELECT dedupe_key, site_address FROM notices").fetchall())
    assert got == {"ny": "303 Louisiana Avenue Brooklyn, NY, 11207", "fl": None,
                   "ca": "1 Report Row", "ga": None}
    assert report["states"]["FL"] == {"rejected:not_surfaced_unlabeled_role": 1}
    assert report["states"]["CA"] == {"left_to_quality_evidence": 1}
    assert site_address.apply(conn, resolver=object())["changed"] == 0


def test_basis_names_where_an_exported_site_address_came_from():
    quality = {"quality_evidence": {"sites": [
        {"role": "affected_worksite", "address": "1 Report Row"}]}}
    assert site_address.basis({"state": "CA", "site_address": "1 Report Row",
                               "source_details": json.dumps(quality)}) == "quality_evidence"
    assert site_address.basis({"state": "IL", "site_address": "1 Main St"}) == "labeled_site_field"
    assert site_address.basis({"state": "PA", "site_address": "1 Main St"}) == "filed_county_consistent"
    assert site_address.basis({"state": "AZ", "site_address": "1 Main St"}) == "unverified"
    assert site_address.basis({"state": "IL", "site_address": None}) is None
    assert project({"state": "IL", "site_address": "1 Main St"})["site_address_basis"] == \
        "labeled_site_field"


def test_quality_site_in_an_unlisted_community_is_placed_by_its_report_county(tmp_path):
    rows = [("CA", "county", "San Diego County", "", "06073", "San Diego County", "")]
    path = tmp_path / "places.csv.gz"
    with gzip.open(path, "wt", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for state, kind, name, place, county_fips, county, inc in rows:
            writer.writerow({"state": state, "kind": kind, "key": fold(name), "name": name,
                             "place_fips": place, "county_fips": county_fips,
                             "county_name": county, "lat": "33.0", "lon": "-116.8",
                             "incorporated": inc})
    resolver = Resolver(path=path, alias_path=tmp_path / "none.csv")
    details = {"quality_evidence": {"sites": [{
        "role": "affected_worksite", "address": "10931 North Torrey Pines Rd. Somewhere CA 92037",
        "city": None, "county": "San Diego", "state": "CA"}]}}
    row = {"state": "CA", "location": None, "source_details": json.dumps(details)}
    got = resolve_geo(resolver, row)
    assert got["county_fips"] == "06073"
    assert got["geo_basis"] == "county:filed"
