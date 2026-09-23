import json
import hashlib

import pytest

from warnlive.migrate.offline_rebuild import (
    _bln_unresolved, _cached_agencies, _cached_only, _exception, _fingerprints, _read_policy,
    _repair_il_from_cache, _write_exceptions, rebuild,
)


def test_offline_rebuild_requires_explicit_policy(tmp_path):
    with pytest.raises(ValueError, match="no rebuild_policy"):
        _read_policy(tmp_path / "missing.json")
    policy = tmp_path / "rebuild_policy.json"
    policy.write_text(json.dumps({"format": "wrong"}))
    with pytest.raises(ValueError, match="unsupported"):
        _read_policy(policy)


def test_offline_cache_reader_never_fetches(tmp_path):
    source = tmp_path / "cached.pdf"
    assert _cached_only("https://unavailable.example/pdf", source) is None
    source.write_bytes(b"%PDF fixture")
    assert _cached_only("https://unavailable.example/pdf", source) == b"%PDF fixture"


def test_source_only_archive_excludes_occupied_months_without_old_db_policy(
    tmp_path, monkeypatch,
):
    import sqlite3
    from warnlive.migrate import offline_rebuild
    from warnlive.backfill import state_archives

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, notice_date TEXT, effective_date TEXT)"
    )
    conn.execute("INSERT INTO notices VALUES ('raw', 'WI', '2026-01-12', NULL)")
    records = [
        {"dedupe_key": "archive-a", "notice_date": "2025-12-01",
         "effective_date": None, "raw_record_hash": "a", "source_url": "archive://a"},
        {"dedupe_key": "archive-b", "notice_date": "2025-12-30",
         "effective_date": None, "raw_record_hash": "b", "source_url": "archive://b"},
        {"dedupe_key": "overlap", "notice_date": "2026-01-01",
         "effective_date": None, "raw_record_hash": "c", "source_url": "archive://c"},
        {"dedupe_key": "undated", "notice_date": None,
         "effective_date": None, "raw_record_hash": "d", "source_url": "archive://d"},
    ]
    monkeypatch.setitem(state_archives.FETCHERS, "WI", lambda _cache: records)
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: [])
    for state in ("FL", "CA", "MA", "OH"):
        monkeypatch.setitem(state_archives.FETCHERS, state, lambda _cache: [])
    captured = []

    def collect(_conn, groups, _observed_at):
        captured.extend(groups.values())
        return {"new": sum(map(len, groups.values())), "updated": 0,
                "unchanged": 0, "coalesced": 0,
                "suspected_collisions": 0}

    monkeypatch.setattr(offline_rebuild, "_ingest_groups", collect)
    exceptions = []
    report = _cached_agencies(conn, tmp_path, None, {}, "2026-09-22", exceptions)
    assert [row["dedupe_key"] for group in captured for row in group] == [
        "archive-a", "archive-b",
    ]
    assert report["WI"]["source_overlap_rows"] == 1
    assert report["WI"]["undated_rows"] == 1
    assert report["WI"]["outside_policy"] == 0
    assert report["WI"]["coalesced_identical_rows"] == 0
    assert report["WI"]["unaccounted_rows"] == 0
    assert [item["reason"] for item in exceptions] == [
        "occupied_source_month", "no_date",
    ]


def test_exception_manifest_is_stable_and_never_overwritten(tmp_path):
    path = tmp_path / "exceptions.jsonl"
    rows = [
        {"origin": "b", "state": "WI", "dedupe_key": "2",
         "raw_record_hash": "b", "reason": "overlap"},
        {"origin": "a", "state": "CA", "dedupe_key": "1",
         "raw_record_hash": "a", "reason": "overlap"},
    ]
    first = _write_exceptions(path, rows)
    second = _write_exceptions(tmp_path / "second.jsonl", list(reversed(rows)))
    assert first["rows"] == 2
    assert first["sha256"] == second["sha256"]
    assert json.loads(path.read_text().splitlines()[0])["state"] == "CA"
    with pytest.raises(FileExistsError):
        _write_exceptions(path, rows)
    assert first["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_california_exception_points_to_bundled_pdf():
    item = _exception("agency-cache:CA", "conflicting_same_key", {
        "state": "CA", "raw_extra": json.dumps({"year_file": 2007}),
        "source_url": "cached://annual-pdf",
    })
    assert item["bundle_artifact"] == "backfill/cache/archives/ca/2007.pdf"


def test_bln_exception_triage_does_not_auto_merge_same_signature(tmp_path):
    import csv
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (state TEXT, notice_date TEXT, effective_date TEXT, "
        "employer_name TEXT, employees_affected INTEGER, dedupe_key TEXT, location TEXT)"
    )
    conn.execute(
        "INSERT INTO notices VALUES ('KS','2026-02-18',NULL,'Same Co',50,'source-key','Wichita')"
    )
    source = tmp_path / "bln.csv"
    with source.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "postal_code", "company", "location", "notice_date", "jobs",
        ])
        writer.writeheader()
        writer.writerow({"postal_code": "KS", "company": "Same Co",
                         "location": "Wichita", "notice_date": "2026-02-18", "jobs": "50"})
        writer.writerow({"postal_code": "KS", "company": "Other Co",
                         "location": "Wichita", "notice_date": "2026-02-18", "jobs": "50"})
    exceptions = []
    report = _bln_unresolved(conn, source, exceptions)
    assert report["total_rows"] == 2
    assert report["represented_by_key"] == 0
    assert report["unaccounted_rows"] == 0
    assert report["unresolved_rows"] == 2
    assert report["triage"] == {
        "no_exact_signature": 1, "one_exact_signature_same_location": 1,
    }
    assert exceptions[0]["candidate_keys"] == ["source-key"]
    assert exceptions[1]["candidate_keys"] == []


def test_bln_superseded_and_identity_excluded_rows_are_accounted_for(tmp_path):
    import csv
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (state TEXT, notice_date TEXT, effective_date TEXT, "
        "employer_name TEXT, employees_affected INTEGER, dedupe_key TEXT, location TEXT)"
    )
    source = tmp_path / "bln.csv"
    with source.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "postal_code", "company", "notice_date", "is_superseded", "hash_id",
        ])
        writer.writeheader()
        writer.writerow({"postal_code": "KS", "company": "Old Co",
                         "notice_date": "2001-01-01", "is_superseded": "True",
                         "hash_id": "old"})
        writer.writerow({"postal_code": "IA", "company": "Phase Co",
                         "notice_date": "2020-01-01", "is_superseded": "False",
                         "hash_id": "phase"})
    exceptions = []
    report = _bln_unresolved(conn, source, exceptions)
    assert report["superseded_rows"] == 1
    assert report["identity_excluded_rows"] == 1
    assert [item["reason"] for item in exceptions] == [
        "superseded_source_row", "source_identity_unresolved",
    ]


def test_source_only_bln_import_enforces_identity_quarantine(monkeypatch):
    from warnlive.migrate import offline_rebuild

    selected_states = []

    def select(_source, _conn, _registry, *, states):
        selected_states.append(set(states))
        return {}

    monkeypatch.setattr(offline_rebuild.bln_integrated, "older_rows_by_state", select)
    monkeypatch.setattr(offline_rebuild.bln_integrated, "gap_rows_by_state", select)
    report = offline_rebuild._bln_conservative(None, None, "2026-09-22")
    assert len(selected_states) == 2
    assert all(not {"ga", "sc", "ia"} & states for states in selected_states)
    assert all("ks" in states for states in selected_states)
    assert report["older"]["input_rows"] == report["empty_months"]["input_rows"] == 0


def test_bln_older_backfill_never_ingests_superseded_rows(tmp_path):
    import csv
    import sqlite3
    from warnlive.backfill.bln_integrated import older_rows_by_state
    from warnlive.registry import load_registry

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE notices (state TEXT, notice_date TEXT)")
    conn.execute("INSERT INTO notices VALUES ('KS', '2020-01-01')")
    source = tmp_path / "bln.csv"
    with source.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "postal_code", "company", "notice_date", "is_superseded",
        ])
        writer.writeheader()
        writer.writerow({"postal_code": "KS", "company": "Old Co",
                         "notice_date": "2000-01-01", "is_superseded": "True"})
        writer.writerow({"postal_code": "KS", "company": "Current Co",
                         "notice_date": "2001-01-01", "is_superseded": "False"})
    result = older_rows_by_state(source, conn, load_registry(), states=["ks"])
    assert [record["employer_name"] for record in result["KS"]] == ["Current Co"]


def test_offline_rebuild_refuses_to_replace_existing_database(tmp_path):
    db = tmp_path / "current.sqlite"
    db.write_bytes(b"preserve")
    with pytest.raises(FileExistsError, match="already exists"):
        rebuild(tmp_path / "missing.tar.gz", db, "2026-09-22")
    assert db.read_bytes() == b"preserve"


def test_source_only_rejects_exception_path_equal_to_database(tmp_path):
    db = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="must differ"):
        rebuild(tmp_path / "missing.tar.gz", db, "2026-09-22",
                source_only=True, exceptions_path=db)
    assert not db.exists()


def test_il_repair_uses_only_cached_reports(tmp_path, monkeypatch):
    from warnlive.cli import il_effective_dates
    from warnlive.enrich import il_effective

    cache = tmp_path / "cache" / "il_reports"
    cache.mkdir(parents=True)
    (cache / "one.PDF").write_bytes(b"cached")
    (cache / "ignore.txt").write_text("not a report")
    parsed = []
    monkeypatch.setattr(il_effective, "parse_report", lambda path: parsed.append(path.name) or [])

    def check_callback(_years, workdir, _db, _dry_run, observed_at):
        assert workdir == tmp_path
        assert observed_at == "2026-09-22"
        assert il_effective.collect_records(set(), cache) == []

    monkeypatch.setattr(il_effective_dates, "callback", check_callback)
    report = _repair_il_from_cache(tmp_path / "candidate.sqlite", cache, "2026-09-22")
    assert parsed == ["one.PDF"]
    assert report["cached_files"] == 1
    assert report["parsed_records"] == 0
    assert report["failed_files"] == []


def test_il_repair_skips_an_optional_missing_cache(tmp_path):
    report = _repair_il_from_cache(
        tmp_path / "candidate.sqlite", tmp_path / "cache" / "il_reports"
    )
    assert report["cached_files"] == 0
    assert report["result"].startswith("skipped:")


def test_il_repair_pins_version_observation_time(tmp_path, monkeypatch):
    from warnlive.cli import il_effective_dates
    from warnlive.enrich import il_effective
    from warnlive.normalize.engine import _dedupe_key, _record_hash
    from warnlive.store import db
    from warnlive.store.dedupe import ingest

    path = tmp_path / "candidate.sqlite"
    conn = db.connect(path)
    db.init_db(conn)
    rec = {
        "state": "IL", "employer_name": "Example Company",
        "location": "Chicago, IL 60601", "notice_date": "2023-01-30",
        "effective_date": None, "employees_affected": 100,
        "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.test/notice", "source_notice_id": "one",
        "raw_extra": "{}",
    }
    rec["dedupe_key"] = _dedupe_key(rec)
    rec["raw_record_hash"] = _record_hash(rec)
    ingest(conn, [rec], "2026-09-22")
    conn.close()
    item = il_effective.ReportRecord(
        company="Example Company", address="", city_state_zip="Chicago IL 60601",
        notified="2023-01-30", first_layoff="2023-03-31",
        ending_layoff=None, source_file="February_2023_Monthly_WARN_Report.xlsx",
    )
    monkeypatch.setattr(il_effective, "collect_records", lambda _years, _cache: [item])
    il_effective_dates.callback("", tmp_path, path, False, "2026-09-22")
    conn = db.connect(path)
    try:
        assert conn.execute(
            "SELECT effective_date FROM notices WHERE state='IL'"
        ).fetchone()[0] == "2023-03-31"
        assert conn.execute(
            "SELECT observed_at FROM notice_versions WHERE version=2"
        ).fetchone()[0] == "2026-09-22T00:00:00Z"
    finally:
        conn.close()


def test_rebuild_fingerprints_exclude_observation_metadata():
    class FixedRows:
        def execute(self, query):
            assert "first_seen" not in query
            assert "last_seen" not in query
            assert "observed_at" not in query
            assert "created_at" not in query
            return [("key", 1)]

    expected = hashlib.sha256(b'["key",1]\n').hexdigest()
    assert _fingerprints(FixedRows()) == {
        "notices": expected, "versions": expected, "links": expected,
    }
