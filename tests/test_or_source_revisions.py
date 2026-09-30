import hashlib
import json

from warnlive.migrate import or_source


def test_identical_cached_copies_do_not_hide_changed_live_capture(monkeypatch, tmp_path):
    original = {"WARN#": 123, "Company Name": "Example", "Location": "Portland",
                "Layoff Date": "2020-01-01", "Laid Off": 127,
                "Layoff Type": "Closure", "Received Date": "2019-11-01"}
    updated = {**original, "Laid Off": 2}
    rows = [
        {"source_row": 4, "source_artifact": "agency/or/latest.xlsx", "snapshot": "cached",
         "source_sha256": "cached", "raw": original, "source_row_sha256": "row4"},
        {"source_row": 5, "source_artifact": "agency/or/latest.xlsx", "snapshot": "cached",
         "source_sha256": "cached", "raw": original, "source_row_sha256": "row5"},
        {"source_row": 4, "source_artifact": "agency/or/live.xlsx", "snapshot": "live",
         "source_sha256": "live", "raw": updated, "source_row_sha256": "live4"},
    ]
    monkeypatch.setattr(or_source, "read_artifacts", lambda _: (rows, {"source_url": "https://example.org"}))
    records, held, report = or_source.project(tmp_path)
    # The cached notice, then the changed live capture as its next version.
    assert [row["employees_affected"] for row in records] == [127, 2]
    assert len({row["dedupe_key"] for row in records}) == 1
    assert report["capture_versions"] == 1 and report["admitted"] == 1
    assert json.loads(records[1]["source_details"])["capture_version"]["basis"] == "agency_capture_order"
    assert [row["reason"] for row in held] == ["duplicate_agency_capture"]
    assert report["admitted_rows"] + len(held) == 3


def _row(ordinal, raw, snapshot="cached"):
    artifact = "agency/or/live.xlsx" if snapshot == "live" else "agency/or/latest.xlsx"
    return {"source_row": ordinal, "source_artifact": artifact, "snapshot": snapshot,
            "captured_on": None, "source_sha256": snapshot, "raw": raw,
            "source_row_sha256": f"{snapshot}{ordinal}"}


def test_changed_capture_with_distant_action_date_stays_held(monkeypatch, tmp_path):
    original = {"WARN#": 7, "Company Name": "Example", "Location": "Salem",
                "Layoff Date": "2020-01-01", "Laid Off": 50,
                "Layoff Type": "Reduction", "Received Date": "2019-11-01"}
    rows = [_row(4, original), _row(4, {**original, "Layoff Date": "2020-06-01"}, "live")]
    monkeypatch.setattr(or_source, "read_artifacts", lambda _: (rows, {"source_url": "u"}))
    records, held, report = or_source.project(tmp_path)
    assert len(records) == 1 and report["capture_versions"] == 0
    assert [(row["reason"], row["disposition"]) for row in held] == [
        ("later_capture_of_existing_warn_number", "unresolved")]


def test_multi_site_warn_number_is_one_itemized_filing(monkeypatch, tmp_path):
    base = {"WARN#": 9, "Received Date": "2021-01-04", "Layoff Type": "Permanent closure"}
    rows = [
        _row(4, {**base, "Company Name": "Acme Portland", "Location": "Portland",
                 "Layoff Date": "2021-03-01", "Laid Off": 40}),
        _row(5, {**base, "Company Name": "Acme Bend", "Location": "Bend",
                 "Layoff Date": "2021-04-15", "Laid Off": 12}),
        _row(6, {**base, "WARN#": 10, "Company Name": "Other", "Location": "Salem",
                 "Layoff Date": "2021-03-01", "Laid Off": 5}),
        _row(7, {**base, "WARN#": 10, "Company Name": "Other Two", "Location": "Salem",
                 "Layoff Date": "2021-03-01", "Laid Off": 6}),
    ]
    monkeypatch.setattr(or_source, "read_artifacts", lambda _: (rows, {"source_url": "u"}))
    records, held, report = or_source.project(tmp_path)
    assert len(records) == 1
    filing = records[0]
    assert filing["dedupe_key"] == hashlib.sha1(b"OR|agency|9").hexdigest()
    assert filing["employer_name"] == "Acme Portland"
    assert filing["location"] is None
    assert filing["employees_affected"] == 52
    assert (filing["effective_date"], filing["effective_date_end"]) == ("2021-03-01", "2021-04-15")
    assert (filing["layoff_type"], filing["is_temporary"]) == ("closure", 0)
    details = json.loads(filing["source_details"])
    assert details["worker_allocation"] == "itemized"
    assert details["effective_date_interpretation"] == "list_or_phases"
    assert [(site["location"], site["workers"]) for site in details["sites"]] == [
        ("Portland", 40), ("Bend", 12)]
    # Unmarked conflicting rows under one number stay held.
    assert {row["reason"] for row in held} == {"multi_site_or_phase_identity_unresolved"}
    assert report["admitted_rows"] == 2 and len(held) == 2


def test_layoff_type_matches_the_live_mapping():
    assert or_source.type_fields("Permanent closure")[:2] == ("closure", 0)
    assert or_source.type_fields("Reduction")[:2] == ("mass_layoff", None)
    assert or_source.type_fields("Large Layoff - 10 or more workers")[:2] == ("mass_layoff", None)
    assert or_source.type_fields("Temporary Layoff")[:2] == ("mass_layoff", 1)
    assert or_source.type_fields("Other")[:2] == ("unknown", None)
    assert or_source.type_fields(None) == ("unknown", None, None)
