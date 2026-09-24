"""Prior agency worker counts may fill only the same Kansas source record."""

import json

import pytest

from warnlive.migrate.ks_source import apply_prior_worker_counts
from warnlive.normalize.engine import _record_hash
from warnlive.store import db
from warnlive.store.dedupe import ingest


def _record(identity: str, workers: int | None) -> dict:
    rec = {
        "dedupe_key": f"key-{identity}", "state": "KS", "source_identity": identity,
        "employer_name": "Boeing Co.", "location": "Area IV",
        "notice_date": "1998-11-30", "effective_date": None,
        "employees_affected": workers, "layoff_type": "unknown",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://www.kansasworks.com/", "source_notice_id": "hash",
        "source_details": json.dumps({"source_record_number": identity[3:]}),
        "raw_extra": json.dumps({"record_number": identity[3:], "workers": workers}),
    }
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_prior_agency_count_fills_blank_with_versioned_evidence(tmp_path):
    with db.connect(tmp_path / "warn.sqlite") as conn:
        db.init_db(conn)
        current = _record("KS:30", None)
        archive = _record("KS:30", 98)
        ingest(conn, [current], "2026-09-22")

        report = apply_prior_worker_counts(conn, [current], [archive], "2026-09-22")

        assert report == {"filled_rows": 1, "restored_reported_workers": 98}
        row = conn.execute(
            "SELECT employees_affected, source_details, current_version, is_amended "
            "FROM notices WHERE source_identity='KS:30'"
        ).fetchone()
        assert row["employees_affected"] == 98
        assert row["current_version"] == 2
        assert row["is_amended"] == 0
        evidence = json.loads(row["source_details"])["worker_count_evidence"]
        assert evidence["basis"] == "prior_agency_capture"
        assert evidence["reported_workers"] == 98
        assert len(evidence["source_row_sha256"]) == 64
        assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
        conn.commit()
        assert apply_prior_worker_counts(conn, [current], [archive], "2026-09-22") == {
            "filled_rows": 0, "restored_reported_workers": 0,
        }
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_prior_count_rejects_changed_record_identity(tmp_path):
    with db.connect(tmp_path / "warn.sqlite") as conn:
        db.init_db(conn)
        current = _record("KS:30", None)
        archive = {**_record("KS:30", 98), "notice_date": "1998-12-01"}
        ingest(conn, [current], "2026-09-22")

        with pytest.raises(ValueError, match="changed employer or notice day"):
            apply_prior_worker_counts(conn, [current], [archive], "2026-09-22")

        assert conn.execute("SELECT employees_affected FROM notices").fetchone()[0] is None


def test_prior_count_does_not_replace_conflicting_current_value(tmp_path):
    with db.connect(tmp_path / "warn.sqlite") as conn:
        db.init_db(conn)
        current = _record("KS:30", 100)
        archive = _record("KS:30", 98)
        ingest(conn, [current], "2026-09-22")

        with pytest.raises(ValueError, match="worker count conflict"):
            apply_prior_worker_counts(conn, [current], [archive], "2026-09-22")

        assert conn.execute("SELECT employees_affected FROM notices").fetchone()[0] == 100


def test_prior_count_late_conflict_leaves_earlier_record_unchanged(tmp_path):
    with db.connect(tmp_path / "warn.sqlite") as conn:
        db.init_db(conn)
        first = _record("KS:30", None)
        second = _record("KS:31", None)
        ingest(conn, [first, second], "2026-09-22")
        archive_first = _record("KS:30", 98)
        archive_second = {**_record("KS:31", 404), "employer_name": "Different"}

        with pytest.raises(ValueError, match="changed employer or notice day"):
            apply_prior_worker_counts(
                conn, [first, second], [archive_first, archive_second], "2026-09-22"
            )

        assert conn.execute("SELECT COUNT(*) FROM notices WHERE employees_affected IS NULL").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_prior_count_leaves_commit_control_with_caller(tmp_path):
    with db.connect(tmp_path / "warn.sqlite") as conn:
        db.init_db(conn)
        current = _record("KS:30", None)
        archive = _record("KS:30", 98)
        ingest(conn, [current], "2026-09-22")
        assert not conn.in_transaction

        apply_prior_worker_counts(conn, [current], [archive], "2026-09-22")
        assert conn.in_transaction
        conn.rollback()

        assert conn.execute("SELECT employees_affected FROM notices").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 1
