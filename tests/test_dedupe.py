import sqlite3

import pytest

from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest


@pytest.fixture()
def conn(tmp_path):
    conn = db_mod.connect(tmp_path / "test.sqlite")
    db_mod.init_db(conn)
    return conn


def record(**overrides):
    base = {
        "state": "CT",
        "employer_name": "Acme Corp",
        "location": "Hartford, CT",
        "notice_date": "2026-06-01",
        "effective_date": "2026-08-01",
        "employees_affected": 120,
        "layoff_type": "closure",
        "is_temporary": None,
        "is_amendment": 0,
        "source_url": "https://example.gov",
        "source_notice_id": "abc123",
        "raw_extra": "{}",
        "dedupe_key": "key-1",
        "raw_record_hash": "hash-1",
    }
    base.update(overrides)
    return base


def test_ingest_is_idempotent(conn):
    stats1 = ingest(conn, [record()], "2026-07-01")
    assert (stats1.new, stats1.updated) == (1, 0)
    stats2 = ingest(conn, [record()], "2026-07-08")
    assert (stats2.new, stats2.updated, stats2.unchanged) == (0, 0, 1)
    row = conn.execute("SELECT first_seen, last_seen, current_version FROM notices").fetchone()
    assert row["first_seen"] == "2026-07-01"
    assert row["last_seen"] == "2026-07-08"
    assert row["current_version"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM notice_versions").fetchone()["c"] == 1


def test_amendment_creates_version(conn):
    ingest(conn, [record()], "2026-07-01")
    amended = record(employees_affected=200, raw_record_hash="hash-2")
    stats = ingest(conn, [amended], "2026-07-08")
    assert stats.updated == 1
    row = conn.execute(
        "SELECT employees_affected, is_amended, current_version FROM notices"
    ).fetchone()
    assert row["employees_affected"] == 200
    assert row["is_amended"] == 1
    assert row["current_version"] == 2
    assert conn.execute("SELECT COUNT(*) c FROM notice_versions").fetchone()["c"] == 2


def test_duplicate_keys_within_batch_collapse(conn):
    stats = ingest(conn, [record(), record(employees_affected=999, raw_record_hash="x")], "2026-07-01")
    assert stats.new == 1
    row = conn.execute("SELECT employees_affected FROM notices").fetchone()
    assert row["employees_affected"] == 120  # first occurrence wins
