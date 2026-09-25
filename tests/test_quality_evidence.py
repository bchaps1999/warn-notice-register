import csv
import json

from warnlive.enrich.notice_quality import geo_location, project
from warnlive.migrate.quality_evidence import (
    DEFAULT_ROOT, _VOLTA, _attach_labeled_sites, _attach_volta,
    _mark_unrecovered, verify,
)
from warnlive.normalize.details import extract
from warnlive.normalize.engine import _dedupe_key
from warnlive.registry import load_registry
from warnlive.store import db
from warnlive.store.dedupe import ingest
from warnlive.store.export import export_csvs
from warnlive.store.site_export import build_site
from warnlive.enrich.site_address import clean_address


def _record(key, state, location, raw=None):
    from warnlive.normalize.engine import _record_hash

    rec = {
        "dedupe_key": key, "state": state,
        "employer_name": "Volta Charging Industries" if state in {"WI", "FL"} else "Example",
        "location": location, "notice_date": "2024-04-01",
        "effective_date": "2024-05-31", "employees_affected": 1,
        "layoff_type": "mass_layoff", "is_temporary": 0,
        "is_amendment": 0, "source_url": "https://example.gov",
        "source_notice_id": key, "raw_extra": json.dumps(raw or {}),
    }
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_volta_letter_roles_are_versioned_and_idempotent(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    records = [_record(key, state, location)
               for key, (state, location, _) in _VOLTA.items()]
    ingest(conn, records, "2026-09-24")
    manifest = verify(DEFAULT_ROOT)
    result = _attach_volta(conn, DEFAULT_ROOT, manifest, "2026-09-24",
                           {"WI", "FL"}, True)
    assert result == {"matched": 4, "changed": 4}
    assert _attach_volta(conn, DEFAULT_ROOT, manifest, "2026-09-24",
                         {"WI", "FL"}, True)["changed"] == 0
    wi_key = next(key for key, value in _VOLTA.items() if value[0] == "WI")
    row = dict(conn.execute("SELECT * FROM notices WHERE dedupe_key=?", (wi_key,)).fetchone())
    assert row["dedupe_key"] == wi_key
    assert row["location"] == "San Francisco"
    assert row["notice_date"] is None
    assert row["current_version"] == 2
    assert geo_location(row) == "Lake Geneva"
    fields = project(row)
    assert fields["affected_site_city"] == "Lake Geneva"
    assert fields["employer_mailing_address"] == "155 De Haro Street San Francisco, CA 94103"
    assert fields["letter_date"] == "2024-03-29"
    assert fields["agency_received_date"] == "2024-04-01"
    assert fields["employer_name_verbatim"] == "Volta Charging Industries, LLC"


def test_labeled_nc_site_and_receipt_are_kept_separate(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    raw = {"Address 1": "1740 Airport Blvd.", "City": "Wilmington",
           "County": "New Hanover County", "Number affected at this location": "82",
           "Date Received by NC": "1/7/2026", "Warn Number": "202600001"}
    rec = _record("nc-key", "NC", "1740 Airport Blvd. Wilmington", raw)
    ingest(conn, [rec], "2026-09-24")
    report = _attach_labeled_sites(conn, "2026-09-24", {"NC"})
    assert report["matched"] == 1
    row = dict(conn.execute("SELECT * FROM notices WHERE dedupe_key='nc-key'").fetchone())
    assert row["site_address"] == "1740 Airport Blvd. Wilmington"
    assert project(row)["agency_received_date"] == "2026-01-07"
    assert _attach_labeled_sites(conn, "2026-09-24", {"NC"})["changed"] == 0


def test_nc_ordinal_street_and_embedded_city_are_source_sites(tmp_path):
    assert clean_address("900 12th Street Drive NW", "NC") == "900 12th Street Drive NW"
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    samples = (
        ("hni", "900 12th Street Drive NW", "Hickory",
         "900 12th Street Drive NW Hickory"),
        ("catalent", "160 Pharma Dr Morrisville NC 27560", "",
         "160 Pharma Dr Morrisville NC 27560"),
    )
    for key, address, city, _ in samples:
        raw = {"Address 1": address, "City": city,
               "County": "Wake County", "Number affected at this location": "84"}
        ingest(conn, [_record(key, "NC", address, raw)], "2026-09-24")
    result = _attach_labeled_sites(conn, "2026-09-24", {"NC"})
    assert result["matched"] == 2
    for key, _, _, expected in samples:
        row = conn.execute("SELECT site_address FROM notices WHERE dedupe_key=?", (key,)).fetchone()
        assert row["site_address"] == expected


def test_nc_multi_address_and_foreign_city_are_held(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    samples = (
        ("52a97e350cb923ccfe97f948297880610834f9d5", "1000 Innovation Ave 1200 Innovation Ave Morrisville NC 27560", ""),
        ("c0d1c1aaf1e45eed55e7ebdc376f18f0eab6e7e7", "420 N. Center St 3560 Dallas Parkway Frisco TX 75034 Hickory NC 28601", ""),
        ("70550648066ef7fb2f2a3f901402444a191b337d", "4200 Morganton Road, Suite 204 2701 N. Rocky Point Drive, Suite 1150 Tampa, Fl 33607 Fayetteville NC 28314", ""),
        ("eb2ceea16bca88470c1b4df6a1725edb3aa4ad7e", "511 Cleveland Street 511 Cleveland Street Durham NC 27702", ""),
        ("fb82b719dd359d33dd295f6228e4be11a8ce3b58", "1855 West State Road 434", "Longwood FL"),
    )
    for key, address, city in samples:
        raw = {"Address 1": address, "City": city, "County": "Wake County",
               "Number affected at this location": "2", "Warn Number": key}
        ingest(conn, [_record(key, "NC", address, raw)], "2026-09-24")
    report = _attach_labeled_sites(conn, "2026-09-24", {"NC"})
    assert report["matched"] == 0
    assert report["reasons"]["ambiguous_labeled_site"] == len(samples)
    for key, address, _ in samples:
        row = dict(conn.execute("SELECT * FROM notices WHERE dedupe_key=?", (key,)).fetchone())
        assert row["site_address"] is None
        assert project(row)["source_status"] == "site_address_ambiguous"
        assert geo_location(row) is None
        assert json.loads(row["source_details"])["quality_evidence"]["raw_site_text"] == address
    export_csvs(conn, tmp_path / "exports", ["nc"])
    with (tmp_path / "exports/warn_notices.csv").open(newline="") as stream:
        exported = list(csv.DictReader(stream))
    assert all(not row["county_fips"] and not row["place_name"] for row in exported)
    out = tmp_path / "site"
    build_site(conn, load_registry(), out, as_of="2026-09-24")
    for key, _, _ in samples:
        item = json.loads((out / "notices" / f"{key[:2]}.json").read_text())[key]
        assert not item["county_fips"] and not item["place_name"]


def test_amended_agency_site_replaces_prior_quality_address(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    raw = {"Address 1": "1740 Airport Blvd.", "City": "Wilmington",
           "County": "New Hanover County", "Number affected at this location": "82",
           "Warn Number": "202600001"}
    first = _record("nc-amend", "NC", "1740 Airport Blvd. Wilmington", raw)
    from warnlive.normalize.engine import _record_hash
    first["raw_record_hash"] = _record_hash(first)
    ingest(conn, [first], "2026-09-24")
    assert _attach_labeled_sites(conn, "2026-09-24", {"NC"})["matched"] == 1
    amended = raw | {"Address 1": "123 Main Street"}
    second = _record("nc-amend", "NC", "123 Main Street Wilmington", amended)
    second["raw_record_hash"] = _record_hash(second)
    ingest(conn, [second], "2026-09-25")
    before = conn.execute("SELECT site_address FROM notices WHERE dedupe_key='nc-amend'").fetchone()
    assert before["site_address"] is None
    report = _attach_labeled_sites(conn, "2026-09-25", {"NC"})
    assert report["matched"] == 1
    after = conn.execute("SELECT site_address FROM notices WHERE dedupe_key='nc-amend'").fetchone()
    assert after["site_address"] == "123 Main Street Wilmington"


def test_csv_and_site_use_verified_remote_city(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    key = next(key for key, value in _VOLTA.items() if value[0] == "WI")
    ingest(conn, [_record(key, "WI", "San Francisco")], "2026-09-24")
    _attach_volta(conn, DEFAULT_ROOT, verify(DEFAULT_ROOT), "2026-09-24",
                  {"WI"}, True)
    export_csvs(conn, tmp_path / "exports", ["wi"])
    with (tmp_path / "exports/warn_notices.csv").open(newline="") as stream:
        exported = next(csv.DictReader(stream))
    assert exported["location"] == "San Francisco"
    assert exported["affected_site_city"] == "Lake Geneva"
    assert exported["place_name"].startswith("Lake Geneva")
    out = tmp_path / "site"
    build_site(conn, load_registry(), out, as_of="2026-09-24")
    shard = json.loads((out / "notices" / f"{key[:2]}.json").read_text())[key]
    assert shard["location"] == "San Francisco"
    assert shard["affected_site_city"] == "Lake Geneva"
    assert shard["place_name"] == exported["place_name"]


def test_unrecovered_source_status_preserves_notice(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    key = "08b04966d29efe1786992f3a67d592ff7b047b78"
    ingest(conn, [_record(key, "CO", "Denver")], "2026-09-24")
    assert _mark_unrecovered(conn, "2026-09-24", {"CO"}) == {
        "matched": 1, "changed": 1}
    row = dict(conn.execute("SELECT * FROM notices WHERE dedupe_key=?", (key,)).fetchone())
    assert project(row)["source_status"] == "specific_source_not_recovered_in_audit"
    assert row["location"] == "Denver" and row["notice_date"] == "2024-04-01"


def test_timing_qc_requires_supported_date_roles():
    base = {"notice_date": "2024-04-23", "effective_date": "2024-04-19",
            "notice_date_precision": "day", "effective_date_precision": "day",
            "notice_date_basis": "reported", "effective_date_basis": "reported"}
    assert project(base)["timing_qc"] == "negative"
    assert project(base | {"notice_date_basis": None})["timing_qc"] is None
    assert project(base | {"effective_date": "2024-04-23"})["timing_qc"] == "same_day"
    assert project(base | {"effective_date": "2025-04-23"})["timing_qc"] == "long_300"


def test_received_and_notification_source_shapes_keep_legacy_keys():
    for state, raw, expected_key in (
        ("WI", {"Notice Received": "20240401"}, "agency_received_date"),
        ("FL", {"State Notification Date": "04-01-24"}, "agency_notification_date"),
    ):
        original = {"state": state, "employer_name": "Volta Charging Industries",
                    "location": "San Francisco", "notice_date": "2024-04-01",
                    "effective_date": "2024-05-31"}
        key = _dedupe_key(original)
        corrected = original | extract(state, raw, original)
        assert corrected["notice_date"] is None
        assert _dedupe_key(corrected) == key
        details = json.loads(corrected["source_details"])
        assert details[expected_key] == "2024-04-01"
        assert project(corrected)["agency_received_date" if state == "WI"
                                  else "agency_notification_date"] == "2024-04-01"
    archive = original | {"state": "FL"}
    assert extract("FL", {"NOTICE DATE": "04/01/2024"}, archive).get("notice_date") is None
