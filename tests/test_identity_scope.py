"""Scoped decisions cannot leak across notices sharing an employer label."""

import csv
import sqlite3

from warnlive.adjudicate import identity
from warnlive.adjudicate.confirm import Confirm
from warnlive.enrich import review
from warnlive.enrich.subsidiaries import Index


def _accepted(name, state, year, location, cik):
    return {
        "normalized_name": name, "scope_state": state,
        "scope_year": year, "scope_location": location,
        "decision": "accept", "cik": str(cik),
    }


def test_override_selection_is_exact_and_conflicts_grant_nothing(tmp_path):
    path = tmp_path / "overrides.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=review.OVERRIDE_FIELDS)
        writer.writeheader()
        writer.writerow(_accepted("acme", "CA", "2020", "San Jose", 11))
        writer.writerow(_accepted("acme", "TX", "2020", "Austin", 22))
        writer.writerow(_accepted("acme", "", "", "", 33))
        writer.writerow({"normalized_name": "legacy", "cik": "44"})
    rows = review.load_override_rows(path)
    assert review.select_override(rows, "acme", "CA", 2020, " SAN   JOSE ")["cik"] == "11"
    assert review.select_override(rows, "acme", "TX", 2020, "Austin")["cik"] == "22"
    assert review.select_override(rows, "acme", "CA", 2021, "San Jose") is None
    assert review.select_override(rows, "acme", "CA", 2020, "Austin") is None
    assert review.select_override(rows, "acme", "CA", None, None) is None
    assert review.select_override(rows, "acme", "", None, None)["cik"] == "33"
    assert review.select_override(rows, "legacy", "", None, None) is None
    rows["acme"].append(_accepted("acme", "CA", "2020", "San Jose", 99))
    assert review.select_override(rows, "acme", "CA", 2020, "San Jose") is None


def test_identity_writer_upgrades_legacy_header_and_deduplicates_by_scope(tmp_path):
    path = tmp_path / "overrides.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["normalized_name", "decision", "cik"])
        writer.writeheader()
        writer.writerow({"normalized_name": "acme", "decision": "", "cik": "77"})
    staging = tmp_path / "staging.csv"
    kwargs = {"overrides_path": path, "subsidiary_path": tmp_path / "parents.csv",
              "staging_path": staging}
    first = identity.write([
        {"override": _accepted("acme", "CA", "2020", "San Jose", 11)},
        {"override": _accepted("acme", "TX", "2020", "Austin", 22)},
    ], **kwargs)
    assert first == (2, 0, 0)
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        assert "scope_location" in reader.fieldnames
    assert len(rows) == 3 and rows[0]["cik"] == "77"
    assert rows[0]["scope_state"] == ""
    duplicate = identity.write([
        {"override": _accepted("acme", "CA", "2020", "San Jose", 99)},
    ], **kwargs)
    assert duplicate == (0, 0, 1)
    with open(path, newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 3


def test_parent_override_selection_is_scoped_and_legacy_rows_are_not_evidence(tmp_path):
    path = tmp_path / "parents.csv"
    fields = identity.SUBSIDIARY_FIELDS
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"normalized_name": "first transit", "parent_cik": "1"})
        writer.writerow({"normalized_name": "first transit", "scope_state": "CA",
                         "scope_year": "2020", "scope_location": "Oakland",
                         "decision": "accept", "parent_cik": "2", "parent_name": "Parent B"})
    index = Index(path=tmp_path / "absent.csv.gz", overrides_path=path)
    assert index.scoped_parent("first transit", "CA", 2020, "Oakland")["parent_cik"] == "2"
    assert index.scoped_parent("first transit", "CA", 2020, "Fresno") is None
    assert index.scoped_parent("first transit", "", None, None) is None


def test_identity_queue_separates_same_name_by_state_year_and_location(tmp_path, monkeypatch):
    class BareAnnotator:
        def annotate(self, *args, **kwargs):
            return {key: None for key in ("cik", "ein", "lei", "wikidata_qid")}

    monkeypatch.setattr(identity, "Annotator", BareAnnotator)
    monkeypatch.setattr(identity.places, "PATH", tmp_path / "missing-places.csv.gz")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (id INTEGER, employer_name TEXT, state TEXT, "
                 "location TEXT, notice_date TEXT, effective_date TEXT, "
                 "employees_affected INTEGER, current_version INTEGER)")
    conn.execute("CREATE TABLE notice_versions (notice_id INTEGER, version INTEGER, fields_json TEXT)")
    conn.executemany("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?)", [
        (1, "Acme Inc", "CA", "San Jose", "2020-01-01", None, 10, 1),
        (2, "Acme Inc", "CA", "Fresno", "2020-02-01", None, 20, 1),
        (3, "Acme Inc", "CA", "San Jose", "2021-01-01", None, 30, 1),
        (4, "Acme Inc", "TX", "San Jose", "2020-01-01", None, 40, 1),
    ])
    rows = identity.load_queue(conn)
    assert len(rows) == 4
    assert {identity.Identity.key(None, row) for row in rows} == {
        '["acme","CA","2020","san jose"]',
        '["acme","CA","2020","fresno"]',
        '["acme","CA","2021","san jose"]',
        '["acme","TX","2020","san jose"]',
    }
    a, b = rows[:2]
    worker = object.__new__(Confirm)
    assert worker.key({**a, "matched_cik": 11}) != worker.key(
        {**b, "matched_cik": 11}
    )
