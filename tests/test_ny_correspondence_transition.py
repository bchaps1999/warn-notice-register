"""Released NY dashboard keys retired into a control-number filing are mapped."""

import csv
import json
import sqlite3

from warnlive.migrate.ny_correspondence_transition import check, transitions, write


def _released(path, rows):
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "dedupe_key", "state", "employer_name", "notice_date", "employees_affected",
            "source_notice_id"])
        writer.writeheader()
        writer.writerows(rows)


def _candidate(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE notices (id INTEGER PRIMARY KEY, dedupe_key TEXT, state TEXT);
        CREATE TABLE notice_versions (notice_id INTEGER, version INTEGER, fields_json TEXT);
    """)
    conn.execute("INSERT INTO notices VALUES (1, 'filing', 'NY'), (2, 'kept', 'NY')")
    evidence = {"ny_dashboard_correspondence": {
        "basis": "same_employer_notice_date_workers_county",
        "source_row": "agency/ny_annual/ny_warn_2015.csv:row:3",
        "raw_cells": ["Acme", "", "2015-04-30", "", "1 Main St", "Erie"]}}
    conn.execute("INSERT INTO notice_versions VALUES (1, 1, ?)", (json.dumps(
        {"employer_name": "ACME", "source_notice_id": "2015-0001", "source_details": "{}"}),))
    conn.execute("INSERT INTO notice_versions VALUES (1, 2, ?)", (json.dumps(
        {"employer_name": "ACME", "source_notice_id": "2015-0001", "location": "Erie",
         "source_details": json.dumps(evidence)}),))
    conn.commit()
    conn.close()


def test_dashboard_key_retired_into_control_number_filing_is_mapped(tmp_path):
    released, db = tmp_path / "released.csv", tmp_path / "candidate.sqlite"
    _released(released, [
        {"dedupe_key": "dashboard", "state": "NY", "employer_name": "Acme",
         "notice_date": "2015-04-30", "employees_affected": "49",
         "source_notice_id": "agency/ny_annual/ny_warn_2015.csv:row:3"},
        {"dedupe_key": "kept", "state": "NY", "employer_name": "Kept",
         "notice_date": "2015-01-01", "employees_affected": "", "source_notice_id": "x"},
        {"dedupe_key": "lost", "state": "NY", "employer_name": "Lost",
         "notice_date": "2015-01-01", "employees_affected": "", "source_notice_id": "y"},
        {"dedupe_key": "other-state", "state": "WI", "employer_name": "W",
         "notice_date": "", "employees_affected": "", "source_notice_id": ""},
    ])
    _candidate(db)
    [row] = transitions(released, db)
    assert (row["retired_dedupe_key"], row["surviving_dedupe_key"], row["surviving_version"]) == (
        "dashboard", "filing", 2)
    assert (row["surviving_source_notice_id"], row["dashboard_county"]) == ("2015-0001", "Erie")
    result = check(released, db, [row])
    assert result["retired_by_map"] == 1
    assert result["missing_unmapped_keys"] == ["lost"]
    assert result["surviving_keys_missing"] == []
    out = tmp_path / "map.csv"
    write([row], out)
    assert next(csv.DictReader(out.open()))["retired_dedupe_key"] == "dashboard"
