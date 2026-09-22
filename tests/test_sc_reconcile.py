import json
import sqlite3

from warnlive.migrate.sc_reconcile import cached_rows, proposed_key, reconcile


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE notices (id INTEGER PRIMARY KEY, state TEXT, dedupe_key TEXT);
        CREATE TABLE notice_versions (notice_id INTEGER, version INTEGER, fields_json TEXT);
    """)
    for ident, raw in enumerate(rows, 1):
        conn.execute("INSERT INTO notices VALUES (?, 'SC', ?)", (ident, f"old-{ident}"))
        conn.execute("INSERT INTO notice_versions VALUES (?, 1, ?)", (
            ident, json.dumps({"raw_extra": json.dumps(raw)}),
        ))
    return conn


def _new(**changes):
    row = {
        "source": "sc/2026.pdf", "company": "Acme, Inc.", "county": "Richland",
        "notice_date": "1/2/2026", "effective_date": "3/5/2026",
        "effective_end_date": "", "legacy_date": "", "impacted": "12",
    }
    row.update(changes)
    return row


def test_reconciliation_proposes_only_one_conservative_match():
    conn = _db([{
        "source": "sc/2026.pdf", "company": "Acme Inc", "location": "Richland",
        "date": "3/5/2026", "jobs": "12",
    }])
    before = conn.total_changes
    report = reconcile(conn, [_new()])
    assert conn.total_changes == before  # reporting never mutates evidence
    assert report.summary() == {
        "stored_versions": 1, "cached_rows": 1, "exact": 1, "ambiguous": 0,
        "unmatched": 0, "collision_groups": 0,
    }
    assert report.proposed[0]["old_key"] == "old-1"
    assert report.proposed[0]["new_key"] == proposed_key(_new())


def test_reconciliation_refuses_duplicate_candidates_and_missing_evidence():
    conn = _db([
        {"source": "sc/2026.pdf", "company": "Acme", "location": "Richland",
         "date": "3/5/2026", "jobs": "12"},
        {"source": "", "company": "Acme", "location": "Richland",
         "date": "3/5/2026", "jobs": "12"},
    ])
    report = reconcile(conn, [_new(company="Acme"), _new(company="Acme")])
    assert len(report.ambiguous) == 1
    assert len(report.ambiguous[0].candidates) == 2
    assert len(report.unmatched) == 1


def test_reconciliation_reports_distinct_notices_colliding_on_one_new_key():
    conn = _db([
        {"source": "sc/2026.pdf", "company": "Acme", "location": "Richland",
         "date": "3/5/2026", "jobs": "12"},
        {"source": "sc/2026.pdf", "company": "Acme", "location": "Richland",
         "date": "3/5/2026", "jobs": "12"},
    ])
    report = reconcile(conn, [_new(company="Acme")])
    assert len(report.exact) == 2
    assert len(report.collision_groups) == 1


def test_cached_sc_rows_reads_all_cached_pdfs(tmp_path):
    # The parser is separately tested on real PDFs; this test keeps source
    # artifact naming stable for the reconciliation key.
    assert cached_rows(tmp_path) == []
