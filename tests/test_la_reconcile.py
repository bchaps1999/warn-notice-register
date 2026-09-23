"""Louisiana reconciliation is an auditable candidate report, not a merge."""

import csv
import sqlite3
from pathlib import Path

from warnlive.migrate.la_reconcile import reconcile


LA = Path(__file__).resolve().parents[1] / "data/source_snapshots/la"


def test_la_reconciliation_retains_candidates_and_superseded_evidence(tmp_path):
    bln = tmp_path / "bln.csv"
    with bln.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "postal_code", "notice_date", "jobs", "hash_id", "company",
            "location", "effective_date", "is_superseded", "is_amendment",
            "likely_ancestor",
        ])
        writer.writeheader()
        writer.writerow({
            "postal_code": "LA", "notice_date": "2025-05-05", "jobs": "71",
            "hash_id": "current", "company": "Cornerstone Chemical Company",
            "location": "Waggaman", "effective_date": "2025-07-31",
            "is_superseded": "False", "is_amendment": "False",
        })
        writer.writerow({
            "postal_code": "LA", "notice_date": "2025-05-05", "jobs": "71",
            "hash_id": "old", "company": "Cornerstone Chemical",
            "location": "Waggaman", "effective_date": "2025-12-31",
            "is_superseded": "True", "is_amendment": "True",
        })
        writer.writerow({
            "postal_code": "TX", "notice_date": "2025-05-05", "jobs": "71",
            "hash_id": "other-state", "company": "Cornerstone Chemical",
        })
    report = reconcile(LA, bln)
    assert report["decision_policy"] == "evidence_only_no_automatic_identity_or_ingest"
    assert len(report["rows"]) == 39
    row = next(item for item in report["rows"] if item["official"]["source_row"] == "2025.pdf:p1:r10")
    assert row["review_bucket"] == "one_active_exact_signature"
    assert [(candidate["hash_id"], candidate["is_superseded"],
             candidate["effective_start_agrees"]) for candidate in row["exact_date_worker_candidates"]] == [
                 ("current", False, True), ("old", True, False),
             ]
    assert [candidate["source_row"] for candidate in row["exact_date_worker_candidates"]] == [1, 2]
    assert report["summary"] == {
        "no_active_exact_signature": 37,
        "one_active_exact_signature": 1,
        "source_annotation": 1,
    }


def test_la_reconciliation_can_audit_candidate_db_without_mutating_it(tmp_path):
    bln = tmp_path / "bln.csv"
    bln.write_text("postal_code,notice_date,jobs\n")
    db = tmp_path / "candidate.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, "
        "location TEXT, notice_date TEXT, effective_date TEXT, "
        "employees_affected INTEGER, source_notice_id TEXT)"
    )
    conn.execute(
        "INSERT INTO notices VALUES (?,?,?,?,?,?,?,?)",
        ("cornerstone", "LA", "Cornerstone Chemical Company", "Waggaman",
         "2025-05-05", "2025-07-31", 71, "source-a"),
    )
    conn.commit()
    conn.close()
    before = db.read_bytes()
    report = reconcile(LA, bln, db)
    assert report["candidate_db_signature_counts"] == {"0": 37, "1": 1}
    row = next(item for item in report["rows"] if item["official"]["source_row"] == "2025.pdf:p1:r10")
    assert row["candidate_db_exact_signature_rows"][0]["dedupe_key"] == "cornerstone"
    assert db.read_bytes() == before
