import sqlite3

import pytest

from warnlive.store import db as db_mod
from warnlive.store.dedupe import CollisionError, append_repair_version, freeze_absent, ingest


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
    assert row["last_seen"] is None  # NULL means present in the latest run
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


def test_source_details_are_versioned_and_projected(conn):
    ingest(conn, [record(effective_date_end="2026-12-31",
                         source_details='{"dates":["2026-12-31"]}')], "2026-07-01")
    ingest(conn, [record(effective_date_end="2027-01-31",
                         source_details='{"dates":["2027-01-31"]}',
                         raw_record_hash="hash-2")], "2026-07-08")
    current = conn.execute(
        "SELECT effective_date_end, source_details FROM notices"
    ).fetchone()
    assert current["effective_date_end"] == "2027-01-31"
    assert "2027-01-31" in current["source_details"]
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


def test_effective_metadata_is_optional_but_versioned_when_present(conn):
    from warnlive.normalize.engine import _record_hash

    original = record()
    baseline_hash = _record_hash(original)
    assert _record_hash(record(
        effective_date_precision=None, effective_date_basis=None,
        effective_date_end_precision=None, effective_date_end_basis=None,
    )) == baseline_hash
    original["raw_record_hash"] = baseline_hash
    ingest(conn, [original], "2026-07-01")
    revised = record(
        effective_date_end="2026-08-31",
        effective_date_precision="day", effective_date_basis="reported",
        effective_date_end_precision="month",
        effective_date_end_basis="reported_month",
    )
    revised["raw_record_hash"] = _record_hash(revised)
    assert revised["raw_record_hash"] != baseline_hash
    assert ingest(conn, [revised], "2026-07-02").updated == 1
    row = conn.execute("SELECT * FROM notices").fetchone()
    assert (row["effective_date_precision"], row["effective_date_basis"],
            row["effective_date_end_precision"], row["effective_date_end_basis"]) == (
                "day", "reported", "month", "reported_month")
    fields = conn.execute(
        "SELECT fields_json FROM notice_versions WHERE notice_id=? AND version=2",
        (row["id"],),
    ).fetchone()[0]
    assert '"effective_date_end_precision": "month"' in fields


def test_canonical_repair_appends_version_without_losing_source_evidence(conn):
    from warnlive.normalize.engine import _record_hash

    original = record()
    original["raw_record_hash"] = _record_hash(original)
    ingest(conn, [original], "2026-07-01")
    notice_id = conn.execute("SELECT id FROM notices").fetchone()[0]
    conn.execute("UPDATE notices SET employer_name = 'Acme Corporation' WHERE id = ?", (notice_id,))
    assert append_repair_version(conn, notice_id, "2026-09-22")
    assert not append_repair_version(conn, notice_id, "2026-09-22")
    row = conn.execute("SELECT current_version, employer_name FROM notices").fetchone()
    assert (row["current_version"], row["employer_name"]) == (2, "Acme Corporation")
    fields = conn.execute(
        "SELECT fields_json FROM notice_versions WHERE notice_id = ? AND version = 2",
        (notice_id,),
    ).fetchone()[0]
    assert '"raw_extra": "{}"' in fields
    assert '"employer_name": "Acme Corporation"' in fields


def test_verbatim_duplicates_within_batch_collapse(conn):
    stats = ingest(conn, [record(), record()], "2026-07-01")
    assert (stats.new, stats.updated, stats.unchanged) == (1, 0, 0)
    assert stats.coalesced == 1
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
    assert row["last_seen"] is None


def test_far_apart_effective_dates_fail_before_any_write(conn):
    """An undated source can hash two distinct filings to one key; the
    second arrives looking like an amendment. The disagreement in effective
    dates must stop the entire batch before any write."""
    ingest(conn, [record(notice_date=None, dedupe_key="k")], "2026-07-01")
    with pytest.raises(CollisionError) as caught:
        ingest(conn, [
            record(dedupe_key="unrelated", raw_record_hash="new"),
            record(notice_date=None, dedupe_key="k", effective_date="2027-03-01",
                   raw_record_hash="hash-2", is_amendment=1),
        ], "2026-07-08")
    assert caught.value.collisions == [{
        "dedupe_key": "k", "scope": "stored_version",
        "earlier_date": "2026-08-01", "incoming_date": "2027-03-01",
    }]
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 1


def test_far_apart_dates_within_batch_fail_even_if_marked_amendment(conn):
    with pytest.raises(CollisionError) as caught:
        ingest(conn, [
            record(),
            record(effective_date="2027-03-01", raw_record_hash="hash-2",
                   is_amendment=1),
        ], "2026-07-01")
    assert caught.value.collisions[0]["scope"] == "batch"
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0


def test_new_version_checks_all_stored_dates_not_only_current(conn):
    ingest(conn, [record()], "2026-07-01")
    ingest(conn, [record(effective_date="2026-09-10", raw_record_hash="hash-2")],
           "2026-07-08")
    with pytest.raises(CollisionError):
        ingest(conn, [record(effective_date="2026-10-20", raw_record_hash="hash-3")],
               "2026-07-15")
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2


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
    assert row["last_seen"] is None
    assert conn.execute("SELECT COUNT(*) c FROM notice_versions").fetchone()["c"] == 2


def test_last_seen_freezes_on_disappearance_and_thaws_on_return(conn):
    """NULL means "present in the latest run"; a date is stamped only when a
    notice leaves its source — the moment the column learns something. This
    is what keeps the daily dump from rewriting most of its rows."""
    ingest(conn, [record(), record(dedupe_key="key-2", employer_name="Beta",
                                   raw_record_hash="hash-b")], "2026-07-01")
    # Beta vanishes from the source on the next run.
    ingest(conn, [record()], "2026-07-08")
    frozen = freeze_absent(conn, "CT", {"key-1"}, "2026-07-01")
    assert frozen == 1
    rows = {r["dedupe_key"]: r["last_seen"] for r in
            conn.execute("SELECT dedupe_key, last_seen FROM notices")}
    assert rows == {"key-1": None, "key-2": "2026-07-01"}
    # Beta returns: unfrozen, written once.
    ingest(conn, [record(dedupe_key="key-2", employer_name="Beta",
                         raw_record_hash="hash-b")], "2026-07-15")
    row = conn.execute(
        "SELECT last_seen FROM notices WHERE dedupe_key='key-2'"
    ).fetchone()
    assert row["last_seen"] is None
