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


def test_verbatim_duplicates_within_batch_collapse(conn):
    stats = ingest(conn, [record(), record()], "2026-07-01")
    assert (stats.new, stats.updated, stats.unchanged) == (1, 0, 0)
    assert conn.execute("SELECT COUNT(*) c FROM notices").fetchone()["c"] == 1


def test_a_differing_duplicate_within_a_batch_is_an_amendment_not_noise(conn):
    """States append amendment rows rather than editing. Two rows under one
    key in one file, with different values, are original and correction —
    the correction must become the current version, not be dropped."""
    stats = ingest(
        conn,
        [record(), record(employees_affected=200, raw_record_hash="hash-2")],
        "2026-07-01",
    )
    assert (stats.new, stats.updated) == (1, 1)
    row = conn.execute(
        "SELECT employees_affected, is_amended, current_version FROM notices"
    ).fetchone()
    assert row["employees_affected"] == 200
    assert row["is_amended"] == 1
    assert row["current_version"] == 2


def test_a_live_url_replaces_an_archive_one_but_never_the_reverse(conn):
    archive = "https://web.archive.org/web/2020/https://example.gov/warn"
    ingest(conn, [record(source_url=archive)], "2026-07-01")
    ingest(conn, [record(source_url="https://example.gov/warn")], "2026-07-08")
    row = conn.execute("SELECT source_url FROM notices").fetchone()
    assert row["source_url"] == "https://example.gov/warn"
    ingest(conn, [record(source_url=archive)], "2026-07-15")
    row = conn.execute("SELECT source_url, last_seen FROM notices").fetchone()
    assert row["source_url"] == "https://example.gov/warn"
    assert row["last_seen"] == "2026-07-15"


def test_far_apart_effective_dates_are_counted_as_a_suspected_collision(conn):
    """An undated source can hash two distinct filings to one key; the
    second arrives looking like an amendment. The disagreement in effective
    dates is the one visible symptom, so it is counted, not swallowed."""
    ingest(conn, [record(notice_date=None, dedupe_key="k")], "2026-07-01")
    stats = ingest(
        conn,
        [record(notice_date=None, dedupe_key="k", effective_date="2027-03-01",
                raw_record_hash="hash-2")],
        "2026-07-08",
    )
    assert stats.suspected_collisions == 1


def test_a_reobserved_older_version_does_not_ping_pong(conn):
    """A source that lists original and amendment re-sends both every run.
    The original matching an *older* version is a re-observation, not a new
    amendment — or every run would add two junk versions per such key."""
    batch = [record(), record(employees_affected=200, raw_record_hash="hash-2")]
    ingest(conn, batch, "2026-07-01")
    stats = ingest(conn, batch, "2026-07-02")
    assert (stats.new, stats.updated, stats.unchanged) == (0, 0, 2)
    row = conn.execute(
        "SELECT employees_affected, current_version, last_seen FROM notices"
    ).fetchone()
    assert row["employees_affected"] == 200
    assert row["current_version"] == 2
    assert row["last_seen"] == "2026-07-02"
    assert conn.execute("SELECT COUNT(*) c FROM notice_versions").fetchone()["c"] == 2
