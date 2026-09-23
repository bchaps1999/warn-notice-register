"""Iowa correspondence nominates evidence without minting filing identity."""

import sqlite3

from warnlive.migrate.ia_reconcile import (
    _candidate_ia_snapshot, _dates_workers, _facts, _match_basis,
    _official_overlap, classify,
)


def _row(**changes):
    return {
        "source_notice_id": "raw-1", "employer_name": "Vero Blue Farms",
        "location": "Webster City", "notice_date": "2018-09-21",
        "effective_date": "2018-10-21", "employees_affected": 24,
    } | changes


def test_correspondence_levels_do_not_merge_cross_location_rows():
    raw = _row()
    by_id = {"raw-1": [raw]}
    by_facts = {_facts(raw): [raw]}
    by_dates_workers = {_dates_workers(raw): [raw]}
    assert classify(_row(), by_id, by_facts, by_dates_workers) == (
        "same_transcription_id", [raw],
    )
    cross_place = _row(source_notice_id="other", location="Fort Dodge")
    assert classify(cross_place, by_id, by_facts, by_dates_workers) == (
        "same_facts_place_disagrees", [raw],
    )
    spelling = _row(source_notice_id="other", employer_name="VeroBlue Farms")
    assert classify(spelling, by_id, by_facts, by_dates_workers) == (
        "same_dates_workers_only", [raw],
    )
    unrelated = _row(source_notice_id="other", notice_date="2018-09-22")
    assert classify(unrelated, by_id, by_facts, by_dates_workers) == (
        "no_raw_counterpart", [],
    )


def test_mixed_place_fact_candidates_have_individual_match_basis():
    clive = _row(source_notice_id="clive", location="Clive")
    ankeny = _row(source_notice_id="ankeny", location="Ankeny")
    official = _row(source_notice_id=None, location="Clive")
    bucket, candidates = classify(
        official, {}, {_facts(official): [clive, ankeny]}, {},
    )
    assert bucket == "same_facts_and_place"
    assert [_match_basis(official, item) for item in candidates] == [
        "same_facts_and_place", "same_facts_place_disagrees",
    ]


def test_official_overlap_preserves_multiple_source_rows():
    item = {"source_row": "current:r7", "source_row_sha256": "one",
            "company_text": "Acme, Inc.", "city_text": "Des Moines",
            "notice_date": "2022-01-01", "effective_date": "2022-03-01",
            "workers_reported": 12}
    other = {**item, "source_row": "current:r8", "source_row_sha256": "two"}
    old = {**item, "source_row": "old:p1:r1", "source_row_sha256": "old"}
    result = _official_overlap([item, other], [old])
    assert result["counts"] == {"multiple_current_counterparts": 1}
    assert [entry["source_row"] for entry in result["rows"][0]["current_candidates"]] == [
        "current:r7", "current:r8",
    ]


def test_candidate_fingerprint_changes_with_reviewed_iowa_content():
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE notices (
        state TEXT, dedupe_key TEXT, source_notice_id TEXT, employer_name TEXT,
        location TEXT, notice_date TEXT, effective_date TEXT,
        effective_date_end TEXT, employees_affected INTEGER, source_identity TEXT,
        source_details TEXT, site_address TEXT, is_amendment INTEGER
    )""")
    conn.execute("""INSERT INTO notices
        (state, dedupe_key, source_notice_id, employer_name, location)
        VALUES ('IA', 'key', 'source', 'Acme', 'Des Moines')""")
    admitted, before = _candidate_ia_snapshot(conn)
    assert admitted == {("key", "source")}
    assert _candidate_ia_snapshot(conn)[1] == before
    conn.execute("UPDATE notices SET location='Ames' WHERE state='IA'")
    assert _candidate_ia_snapshot(conn)[1] != before
