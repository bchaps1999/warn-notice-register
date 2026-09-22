import csv
import json
import sqlite3

from warnlive.migrate.historical_reconcile import reconcile


def _db(state, rows):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE notices (id INTEGER PRIMARY KEY, state TEXT, dedupe_key TEXT);
        CREATE TABLE notice_versions (notice_id INTEGER, version INTEGER, fields_json TEXT);
    """)
    for notice_id, version, raw in rows:
        conn.execute("INSERT OR IGNORE INTO notices VALUES (?, ?, ?)",
                     (notice_id, state, f"old-{notice_id}"))
        conn.execute("INSERT INTO notice_versions VALUES (?, ?, ?)",
                     (notice_id, version, json.dumps({
                         "employer_name": raw.get("Company Name") or raw.get("Company"),
                         "raw_extra": json.dumps(raw),
                     })))
    return conn


def _csv(path, rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fields)
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_ga_report_distinguishes_exact_raw_from_filing_membership(tmp_path):
    source = _csv(tmp_path / "ga.csv", [
        {"GA WARN ID": "GA1", "Company Name": "Acme", "County": "A"},
        {"GA WARN ID": "GA2", "Company Name": "Acme", "County": "B"},
    ])
    conn = _db("GA", [
        (1, 1, {"GA WARN ID": "GA1", "Company Name": "Acme", "County": "A"}),
        (1, 2, {"GA WARN ID": "GA2", "Company Name": "Acme", "County": "old"}),
        (2, 1, {"GA WARN ID": "", "Company Name": "Acme"}),
    ])
    before = conn.total_changes
    report = reconcile(conn, "GA", source)
    assert conn.total_changes == before
    assert [v["status"] for v in report["versions"]] == [
        "exact_raw_candidate", "filing_membership_only", "unmatched_current_snapshot",
    ]
    assert report["summary"]["notices_multiple_explicit_filings"] == 1
    assert report["versions"][1]["candidate_filings"] == ["GA:GA2"]


def test_ia_report_preserves_duplicate_and_missing_evidence(tmp_path):
    raw = {"Company": "Acme", "Address Line 1": "1 Main", "Layoff Date": "3/32/2026"}
    source = _csv(tmp_path / "ia.csv", [raw, raw])
    conn = _db("IA", [(1, 1, raw), (2, 1, {})])
    report = reconcile(conn, "IA", source)
    assert report["versions"][0]["status"] == "duplicate_raw_candidates"
    assert report["versions"][0]["candidate_ordinals"] == [1, 2]
    assert report["versions"][1]["status"] == "missing_original_raw"
    assert report["versions"][0]["evidence"]["raw"]["Layoff Date"] == "3/32/2026"


def test_report_rejects_unsupported_state(tmp_path):
    conn = _db("SC", [])
    try:
        reconcile(conn, "SC", tmp_path / "missing.csv")
    except ValueError as exc:
        assert "GA and IA" in str(exc)
    else:
        assert False, "unsupported state should not be interpreted as GA or IA"
