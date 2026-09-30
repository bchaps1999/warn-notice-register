import json
import hashlib

import pytest

from warnlive.migrate.offline_rebuild import (
    _cached_agencies, _cached_only, _exception, _fingerprints,
    _repair_il_from_cache, _verify_source_row_accounting, _write_exceptions, rebuild,
)


def test_source_row_accounting_rejects_missing_exception():
    report = {
        "raw": {"AL": {"raw_rows": 2, "new": 1, "unaccounted_rows": 0}},
        "cached_agencies": {},
    }
    with pytest.raises(ValueError, match="source-row accounting mismatch"):
        _verify_source_row_accounting(report, [])
    summary = _verify_source_row_accounting(
        report, [{"origin": "raw/al.csv", "prepared_row": 2,
                  "reason": "coalesced_same_key_content"}],
    )
    assert summary["current_raw"] == {"excluded_rows": 1}


def test_source_row_accounting_accepts_sc_coalescence():
    report = {
        "raw": {"SC": {"raw_rows": 2, "new": 1, "unaccounted_rows": 0}},
        "cached_agencies": {},
    }
    summary = _verify_source_row_accounting(report, [
        {"origin": "cache/sc", "prepared_row": 2,
         "reason": "coalesced_same_key_content"},
    ])
    assert summary == {
        "current_raw": {"excluded_rows": 1},
        "agency_cache": {"excluded_rows": 0},
    }


def test_offline_cache_reader_never_fetches(tmp_path):
    source = tmp_path / "cached.pdf"
    assert _cached_only("https://unavailable.example/pdf", source) is None
    source.write_bytes(b"%PDF fixture")
    assert _cached_only("https://unavailable.example/pdf", source) == b"%PDF fixture"


def test_source_only_archive_screens_employer_overlap_not_statewide_month(
    tmp_path, monkeypatch,
):
    import sqlite3
    from warnlive.migrate import offline_rebuild
    from warnlive.backfill import state_archives

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, notice_date TEXT, "
        "effective_date TEXT, source_details TEXT)"
    )
    conn.execute("INSERT INTO notices VALUES ('raw', 'WI', 'Acme', '2026-01-12', NULL, NULL)")
    records = [
        {"dedupe_key": "archive-a", "notice_date": "2025-12-01",
         "effective_date": None, "raw_record_hash": "a", "source_url": "archive://a"},
        {"dedupe_key": "archive-b", "notice_date": "2025-12-30",
         "effective_date": None, "raw_record_hash": "b", "source_url": "archive://b"},
        {"dedupe_key": "overlap", "employer_name": "Acme", "notice_date": "2026-01-01",
         "effective_date": None, "raw_record_hash": "c", "source_url": "archive://c"},
        {"dedupe_key": "same-month-other-employer", "employer_name": "Other Co",
         "notice_date": "2026-01-15", "effective_date": None,
         "raw_record_hash": "e", "source_url": "archive://e"},
        {"dedupe_key": "undated", "notice_date": None,
         "effective_date": None, "raw_record_hash": "d", "source_url": "archive://d"},
    ]
    monkeypatch.setitem(state_archives.FETCHERS, "WI", lambda _cache: records)
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: [])
    for state in ("FL", "CA", "MA", "OH"):
        monkeypatch.setitem(state_archives.FETCHERS, state, lambda _cache: [])
    captured = []

    def collect(_conn, groups, _observed_at, _revision_keys=frozenset()):
        captured.extend(groups.values())
        return {"new": sum(map(len, groups.values())), "updated": 0,
                "unchanged": 0, "coalesced": 0,
                "suspected_collisions": 0}

    monkeypatch.setattr(offline_rebuild, "_ingest_groups", collect)
    exceptions = []
    report = _cached_agencies(conn, tmp_path, "2026-09-22", exceptions)
    assert [row["dedupe_key"] for group in captured for row in group] == [
        "archive-a", "archive-b", "same-month-other-employer",
    ]
    assert report["WI"]["source_overlap_rows"] == 1
    assert report["WI"]["undated_rows"] == 1
    assert report["WI"]["coalesced_identical_rows"] == 0
    assert report["WI"]["unaccounted_rows"] == 0
    assert [item["reason"] for item in exceptions] == [
        "possible_employer_month_overlap", "no_date",
    ]


def test_florida_archive_overlap_uses_notice_month_when_range_is_parsed(
    tmp_path, monkeypatch,
):
    import sqlite3
    from warnlive.migrate import offline_rebuild
    from warnlive.backfill import state_archives

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, notice_date TEXT, "
        "effective_date TEXT, source_details TEXT)"
    )
    conn.execute("INSERT INTO notices VALUES ('raw', 'FL', 'Acme', '2015-03-01', '2015-01-20', NULL)")
    archive = {
        "dedupe_key": "distinct-archive", "state": "FL",
        "notice_date": "2014-11-12", "effective_date": "2015-01-09",
        "effective_date_end": "2015-01-23", "raw_record_hash": "archive",
        "source_url": "archive://fl",
    }
    for state in ("WI", "CA", "MA", "OH"):
        monkeypatch.setitem(state_archives.FETCHERS, state, lambda _cache: [])
    monkeypatch.setitem(state_archives.FETCHERS, "FL", lambda _cache: [archive])
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: [])
    captured = []

    def collect(_conn, groups, _observed_at, _revision_keys=frozenset()):
        captured.extend(row for rows in groups.values() for row in rows)
        return {"new": len(captured), "updated": 0, "unchanged": 0,
                "coalesced": 0, "suspected_collisions": 0}

    monkeypatch.setattr(offline_rebuild, "_ingest_groups", collect)
    report = _cached_agencies(conn, tmp_path, "2026-09-22")
    assert captured == [archive]
    assert report["FL"]["source_overlap_rows"] == 0


def test_massachusetts_archive_overlap_keeps_received_month_after_role_change(
    tmp_path, monkeypatch,
):
    import sqlite3
    from warnlive.migrate import offline_rebuild
    from warnlive.backfill import state_archives

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, notice_date TEXT, "
        "effective_date TEXT, source_details TEXT)"
    )
    conn.execute("INSERT INTO notices VALUES (?, ?, ?, ?, ?, ?)", (
        "current", "MA", "Acme", None, "2025-08-01",
        json.dumps({"agency_received_date": "2025-06-12"}),
    ))
    archive = {
        "dedupe_key": "distinct-archive", "state": "MA", "employer_name": "Acme", "notice_date": None,
        "effective_date": "2025-09-01", "raw_record_hash": "archive",
        "source_url": "archive://ma",
        "source_details": json.dumps({"agency_received_date": "2025-06-01"}),
    }
    for state in ("WI", "FL", "CA", "OH"):
        monkeypatch.setitem(state_archives.FETCHERS, state, lambda _cache: [])
    monkeypatch.setitem(state_archives.FETCHERS, "MA", lambda _cache: [archive])
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: [])
    monkeypatch.setattr(offline_rebuild, "_ingest_groups", lambda *_args: {
        "new": 0, "updated": 0, "unchanged": 0, "coalesced": 0,
        "suspected_collisions": 0,
    })
    report = _cached_agencies(conn, tmp_path, "2026-09-22")
    assert report["MA"]["source_overlap_rows"] == 1
    assert report["MA"]["new"] == 0


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
    assert first["by_state_source_year_reason"] == [
        {"state": "CA", "source": "a", "notice_year": "unknown",
         "reason": "overlap", "rows": 1},
        {"state": "WI", "source": "b", "notice_year": "unknown",
         "reason": "overlap", "rows": 1},
    ]
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


def test_exception_year_uses_only_explicit_notice_date():
    dated = _exception("raw/ca.csv", "conflicting_same_key", {
        "state": "CA", "notice_date": "2024-03-12",
    })
    undated = _exception("raw/nj.csv", "identity_unresolved", {
        "state": "NJ", "effective_date": "2024-03-12",
    })
    assert dated["notice_year"] == "2024"
    assert undated["notice_year"] is None


def test_offline_rebuild_refuses_to_replace_existing_database(tmp_path):
    db = tmp_path / "current.sqlite"
    db.write_bytes(b"preserve")
    with pytest.raises(FileExistsError, match="already exists"):
        rebuild(tmp_path / "missing.tar.gz", db, "2026-09-22", source_only=True)
    assert db.read_bytes() == b"preserve"


def test_source_only_rejects_exception_path_equal_to_database(tmp_path):
    db = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="must differ"):
        rebuild(tmp_path / "missing.tar.gz", db, "2026-09-22",
                source_only=True, exceptions_path=db)
    assert not db.exists()


def _bundle(path, members: dict[str, bytes]):
    import gzip
    import io
    import tarfile

    from warnlive.migrate.source_bundle import _entry

    manifest = {"format": "warn-source-bundle-v1", "admission_inputs": "agency-only-v1",
                "files": [{"path": name, "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(members.items())]}
    with path.open("xb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped, \
            tarfile.open(fileobj=zipped, mode="w") as archive:
        meta = json.dumps(manifest).encode()
        archive.addfile(_entry("manifest.json", meta), io.BytesIO(meta))
        for name, data in sorted(members.items()):
            archive.addfile(_entry(name, data), io.BytesIO(data))
    return path


def test_rebuild_rejects_big_local_news_bundle_with_pointer(tmp_path):
    bundle = _bundle(tmp_path / "bln.tar.gz", {
        "backfill/bln_integrated.csv": b"postal_code,company\nKS,Acme\n",
        "raw/ks.csv": b"Company\nAcme\n",
    })
    db = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="backfill/bln_integrated.csv.*aa1e94c"):
        rebuild(bundle, db, "2026-09-30")
    assert not db.exists()


def test_rebuild_rejects_dashboard_only_new_york_layout(tmp_path):
    bundle = _bundle(tmp_path / "ny.tar.gz", {"agency/ny/manifest.json": b"{}"})
    db = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="agency/ny without agency/ny_annual"):
        rebuild(bundle, db, "2026-09-30")
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
        "location": "Chicago, IL 60601", "notice_date": None,
        "effective_date": None, "employees_affected": 100,
        "layoff_type": "unknown", "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.test/notice", "source_notice_id": "one",
        "raw_extra": json.dumps({"Initial Date Reported": "2023-01-30 00:00:00"}),
        "source_identity": "IL:IEBS:20230130001",
        "source_details": json.dumps({
            "date_evidence_rule": "il_iebs_agency_dates_v1",
            "agency_reported_date": "2023-01-30",
        }),
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
            "SELECT notice_date FROM notices WHERE state='IL'"
        ).fetchone()[0] is None
        assert conn.execute(
            "SELECT observed_at FROM notice_versions WHERE version=2"
        ).fetchone()[0] == "2026-09-22T00:00:00Z"
    finally:
        conn.close()
    il_effective_dates.callback("", tmp_path, path, False, "2026-09-22")
    conn = db.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
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


def test_archive_entries_phases_and_control_numbers_are_accounted(tmp_path, monkeypatch):
    """CA entries, WI phases and updates, and NY control numbers all ingest."""
    from warnlive.backfill import state_archives
    from warnlive.migrate import offline_rebuild
    from warnlive.normalize.engine import _dedupe_key, _record_hash
    from warnlive.store import db as db_mod

    def rec(state, employer, effective, workers, *, notice=None, details=None,
            raw=None, amendment=0):
        row = {"state": state, "employer_name": employer, "location": "Town",
               "notice_date": notice, "effective_date": effective,
               "employees_affected": workers, "layoff_type": "closure",
               "is_temporary": None, "is_amendment": amendment,
               "source_url": f"archive://{state}", "source_notice_id": None,
               "source_details": json.dumps(details, sort_keys=True) if details else None,
               "raw_extra": json.dumps(raw or {"w": workers})}
        row["dedupe_key"] = _dedupe_key(dict(row, notice_date=notice or effective))
        row["raw_record_hash"] = _record_hash(row)
        return row

    def wi(received, effective, workers, amendment=0):
        return rec("WI", "Hostess", effective, workers, amendment=amendment,
                   details={"agency_received_date": received,
                            "legacy_notice_key_date": received})

    ca = [rec("CA", "Abbott", "2014-11-12", n) for n in (9, 41, 53, 41)]
    wi_rows = [wi("2012-05-07", "2012-07-04", 13), wi("2012-05-07", "2012-06-01", 2),
               wi("2013-01-02", "2013-03-01", 50), wi("2013-02-02", "2013-03-01", 50, 1)]
    ny = [rec("NY", "ConAgra", day, 395, notice="2014-03-20",
              raw={"Control Number": "2013-0291", "wayback_id": page})
          for day, page in (("2015-05-24", "4522"), ("2015-02-28", "4709"),
                            ("2015-05-24", "4977"))]
    monkeypatch.setitem(state_archives.FETCHERS, "CA", lambda _cache: ca)
    monkeypatch.setitem(state_archives.FETCHERS, "WI", lambda _cache: wi_rows)
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: ny)
    conn = db_mod.connect(tmp_path / "c.sqlite")
    db_mod.init_db(conn)
    exceptions = []
    report = _cached_agencies(conn, tmp_path, "2026-09-29", exceptions,
                              states=("CA", "WI", "NY"))
    assert {s: report[s]["unaccounted_rows"] for s in report} == {"CA": 0, "WI": 0, "NY": 0}
    counts = dict(conn.execute("SELECT state, COUNT(*) FROM notices GROUP BY state").fetchall())
    assert counts == {"CA": 3, "WI": 2, "NY": 1}
    assert report["WI"]["phase_rows_folded"] == 1 and report["WI"]["updated"] == 1
    assert report["NY"]["updated"] == 1 and report["NY"]["coalesced_identical_rows"] == 1
    phase = conn.execute(
        "SELECT employees_affected, effective_date, effective_date_end FROM notices "
        "WHERE state='WI' AND dedupe_key=?", (wi_rows[0]["dedupe_key"],)).fetchone()
    assert tuple(phase) == (15, "2012-06-01", "2012-07-04")
    assert sorted(item["reason"] for item in exceptions) == [
        "coalesced_same_key_content", "coalesced_same_key_content", "folded_into_filing_phases",
    ]
    ny_current = conn.execute("SELECT current_version, effective_date FROM notices WHERE state='NY'").fetchone()
    assert tuple(ny_current) == (2, "2015-05-24")


def test_source_only_replay_admits_nebraska_2020_2022_archive_once(tmp_path, monkeypatch):
    """NE's pinned 2020-2022 page capture enters the replay as in the live
    backfill; a row already present from raw/ne.csv is never admitted twice."""
    import sqlite3
    from warnlive.migrate import offline_rebuild
    from warnlive.backfill import state_archives

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, notice_date TEXT, "
        "effective_date TEXT, source_details TEXT)"
    )
    conn.execute("INSERT INTO notices VALUES ('raw-key', 'NE', 'Acme', '2022-03-02', NULL, NULL)")
    records = [
        {"dedupe_key": "raw-key", "state": "NE", "employer_name": "Acme",
         "notice_date": "2022-03-02", "raw_record_hash": "a", "source_url": "archive://a"},
        {"dedupe_key": "same-employer-month", "state": "NE", "employer_name": "Acme",
         "notice_date": "2022-03-20", "raw_record_hash": "b", "source_url": "archive://b"},
        {"dedupe_key": "new-2021", "state": "NE", "employer_name": "Other Co",
         "notice_date": "2021-06-01", "raw_record_hash": "c", "source_url": "archive://c"},
    ]
    assert "NE" in offline_rebuild._cached_agencies.__defaults__[-1]
    monkeypatch.setitem(state_archives.FETCHERS, "NE", lambda _cache: records)
    monkeypatch.setattr(offline_rebuild, "_cached_ny", lambda _cache: [])
    for state in ("WI", "FL", "CA", "MA", "OH"):
        monkeypatch.setitem(state_archives.FETCHERS, state, lambda _cache: [])
    captured = []

    def collect(_conn, groups, _observed_at, _revision_keys=frozenset()):
        captured.extend(row["dedupe_key"] for group in groups.values() for row in group)
        return {"new": sum(map(len, groups.values())), "updated": 0,
                "unchanged": 0, "coalesced": 0, "suspected_collisions": 0}

    monkeypatch.setattr(offline_rebuild, "_ingest_groups", collect)
    exceptions = []
    report = _cached_agencies(conn, tmp_path, "2026-09-30", exceptions)
    assert captured == ["new-2021"]
    assert report["NE"]["already"] == 1
    assert report["NE"]["source_overlap_rows"] == 1
    assert report["NE"]["unaccounted_rows"] == 0
    assert sorted(item["reason"] for item in exceptions) == [
        "matched_existing_key_not_admitted", "possible_employer_month_overlap"]


def test_nebraska_archive_reader_is_cache_only(tmp_path):
    """Without the pinned capture in the bundle the NE archive yields nothing
    and makes no request (an older bundle replays as before)."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE notices (dedupe_key TEXT, state TEXT, employer_name TEXT, notice_date TEXT, "
        "effective_date TEXT, source_details TEXT)"
    )
    report = _cached_agencies(conn, tmp_path, "2026-09-30", [], states=("NE",))
    assert report["NE"]["parsed"] == 0
