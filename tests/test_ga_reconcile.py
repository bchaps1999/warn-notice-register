"""Georgia correspondence reports evidence without making identity decisions."""

import json
import sqlite3

import pytest

from warnlive.migrate.ga_reconcile import (
    _bln_match_basis, _candidate_snapshot, _facts, _name_date, _row_hash,
    _partition, classify_bln,
)


def _row(**changes):
    return {
        "employer_name": "Acme, Inc.", "location": "Atlanta",
        "effective_date": "2024-03-01", "employees_affected": 20,
    } | changes


def test_bln_fact_matches_preserve_one_to_many_and_place_disagreement():
    source = _row()
    same = _row(source_row=1)
    other_place = _row(location="Savannah", source_row=2)
    by_facts = {_facts(same): [same, other_place]}
    by_name_date = {_name_date(same): [same, other_place]}
    assert classify_bln(source, by_facts, by_name_date) == (
        "same_facts_and_place", [same, other_place],
    )
    assert [_bln_match_basis(source, item) for item in by_facts[_facts(source)]] == [
        "same_facts_and_place", "same_facts_place_disagrees",
    ]
    assert classify_bln(_row(location="Macon"), by_facts, by_name_date) == (
        "same_facts_place_disagrees", [same, other_place],
    )
    assert classify_bln(_row(employees_affected=21), by_facts, by_name_date) == (
        "same_name_date_workers_disagree", [same, other_place],
    )
    assert classify_bln(_row(effective_date="2024-04-01"), by_facts, by_name_date) == (
        "no_counterpart", [],
    )


def test_candidate_snapshot_binds_identity_and_raw_evidence_without_writes():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE notices (
            id INTEGER PRIMARY KEY, state TEXT, dedupe_key TEXT,
            source_identity TEXT, source_notice_id TEXT, employer_name TEXT,
            location TEXT, notice_date TEXT, effective_date TEXT,
            effective_date_end TEXT, notice_date_precision TEXT,
            notice_date_basis TEXT, source_details TEXT,
            employees_affected INTEGER, layoff_type TEXT, is_temporary INTEGER,
            is_amendment INTEGER, site_address TEXT, source_url TEXT,
            is_amended INTEGER, current_version INTEGER
        );
        CREATE TABLE notice_versions (
            notice_id INTEGER, version INTEGER, fields_json TEXT
        );
    """)
    raw = {"GA WARN ID": "GA1", "Company Name": "Acme"}
    conn.execute("""INSERT INTO notices
        (id, state, dedupe_key, source_identity, employer_name, current_version)
        VALUES (1, 'GA', 'key', 'GA:GA1', 'Acme', 1)""")
    conn.execute("INSERT INTO notice_versions VALUES (1, 1, ?)", (
        json.dumps({"raw_extra": json.dumps(raw)}),
    ))
    before = conn.total_changes
    by_identity, by_raw, by_key, fingerprint, count = _candidate_snapshot(conn)
    assert conn.total_changes == before
    assert count == 1
    assert by_identity["GA:GA1"] == by_raw[_row_hash(raw)] == by_key["key"]
    assert _candidate_snapshot(conn)[-2] == fingerprint
    conn.execute("UPDATE notices SET employer_name='Other' WHERE id=1")
    assert _candidate_snapshot(conn)[-2] != fingerprint
    conn.execute("UPDATE notices SET employer_name='Acme', source_details='{}' WHERE id=1")
    assert _candidate_snapshot(conn)[-2] != fingerprint
    conn.execute("UPDATE notices SET source_details=NULL WHERE id=1")
    conn.execute("UPDATE notice_versions SET fields_json=? WHERE notice_id=1", (
        json.dumps({"raw_extra": json.dumps(raw), "source_details": {"sites": 2}}),
    ))
    assert _candidate_snapshot(conn)[-2] != fingerprint


def test_source_row_hash_is_key_order_independent():
    assert _row_hash({"GA WARN ID": "GA1", "Company Name": "Acme"}) == _row_hash({
        "Company Name": "Acme", "GA WARN ID": "GA1",
    })


def test_historical_and_bln_partitions_reject_missing_source_rows():
    entries = [{"disposition": "missing_id"}, {"disposition": "id_in_raw"}]
    assert _partition(entries, 2, "historical") == {
        "id_in_raw": 1, "missing_id": 1,
    }
    with pytest.raises(ValueError, match="not fully accounted"):
        _partition(entries, 3, "historical")
