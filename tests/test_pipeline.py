from pathlib import Path

from warnlive import pipeline
from warnlive.normalize.engine import NormalizeResult
from warnlive.registry import StateConfig
from warnlive.store import db
from warnlive.verify.harness import VerificationResult


def _config(postal="ct"):
    return StateConfig(
        postal=postal, name="Test state", source="upstream", status="active",
        tier="easy", cadence="daily", needs_browser=False, min_rows=1,
        staleness_days=None, expected_columns=None,
        source_url="https://example.gov", notes="",
    )


def _record():
    return {
        "state": "CT", "employer_name": "Acme", "location": "Hartford",
        "notice_date": "2026-01-01", "effective_date": "2026-03-01",
        "employees_affected": 20, "layoff_type": "closure",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": "source-1",
        "raw_extra": "{}", "dedupe_key": "key-1", "raw_record_hash": "hash-1",
    }


def _setup(monkeypatch, tmp_path: Path, *, cached: bool, failed_rows: int = 0):
    conn = db.connect(tmp_path / "test.sqlite")
    db.init_db(conn)
    raw = tmp_path / "raw" / "ct.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("company\nAcme\n")
    if not cached:
        monkeypatch.setattr(pipeline.fetch, "fetch_state", lambda *_: raw)
    monkeypatch.setattr(
        pipeline.engine, "normalize_file",
        lambda *_: NormalizeResult(
            state="CT", records=[_record()], raw_rows=1 + failed_rows,
            failed_rows=failed_rows,
        ),
    )
    monkeypatch.setattr(
        pipeline.harness, "verify_state",
        lambda *_args, **_kwargs: VerificationResult(state="CT"),
    )
    return conn, raw


def test_cached_snapshot_does_not_freeze_absent(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=True)
    pipeline.dedupe.ingest(conn, [{**_record(), "dedupe_key": "old", "raw_record_hash": "old"}], "2026-01-01")
    result = pipeline._run_one(_config(), conn, raw.parent, tmp_path / "cache", False, True)
    assert result.verdict == "ok"
    assert conn.execute("SELECT last_seen FROM notices WHERE dedupe_key='old'").fetchone()[0] is None


def test_partially_parsed_live_snapshot_does_not_freeze_absent(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=False, failed_rows=1)
    pipeline.dedupe.ingest(conn, [{**_record(), "dedupe_key": "old", "raw_record_hash": "old"}], "2026-01-01")
    result = pipeline._run_one(_config(), conn, raw.parent, tmp_path / "cache", False, False)
    assert result.verdict == "ok"
    assert conn.execute("SELECT last_seen FROM notices WHERE dedupe_key='old'").fetchone()[0] is None


def test_ingest_and_absence_are_atomic(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=False)

    def broken_freeze(*_args, **_kwargs):
        raise RuntimeError("freeze failed")

    monkeypatch.setattr(pipeline.dedupe, "freeze_absent", broken_freeze)
    result = pipeline._run_one(_config(), conn, raw.parent, tmp_path / "cache", False, False)
    assert result.verdict == "failed"
    assert "freeze failed" in result.error
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0


def test_telemetry_failure_rolls_back_state_data(monkeypatch, tmp_path):
    conn, _ = _setup(monkeypatch, tmp_path, cached=False)
    conn.execute(
        "CREATE TRIGGER reject_state_log BEFORE INSERT ON state_runs "
        "BEGIN SELECT RAISE(ABORT, 'telemetry failed'); END"
    )
    conn.commit()
    report = pipeline.run_states(
        conn, None, [_config()], tmp_path, trigger="manual",
    )
    assert report.outcomes[0].verdict == "failed"
    assert "telemetry failed" in report.outcomes[0].error
    second = db.connect(tmp_path / "test.sqlite")
    try:
        assert second.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0
    finally:
        second.close()


def test_old_south_carolina_rows_require_reconciliation(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=False)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, first_seen) "
        "VALUES ('old-sc', 'SC', '2026-01-01')"
    )
    conn.commit()
    result = pipeline._run_one(
        _config("sc"), conn, raw.parent, tmp_path / "cache", False, False,
    )
    assert result.verdict == "failed"
    assert "migration" in result.error
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 1


def test_old_georgia_rows_cannot_mix_with_source_identity_keys(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=False)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, first_seen) "
        "VALUES ('old-ga', 'GA', '2026-01-01')"
    )
    conn.commit()
    result = pipeline._run_one(
        _config("ga"), conn, raw.parent, tmp_path / "cache", False, False,
    )
    assert result.verdict == "failed"
    assert "clean candidate" in result.error
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 1


def test_old_kansas_rows_cannot_mix_with_source_identity_keys(monkeypatch, tmp_path):
    conn, raw = _setup(monkeypatch, tmp_path, cached=False)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, first_seen) "
        "VALUES ('old-ks', 'KS', '2026-01-01')"
    )
    conn.commit()
    result = pipeline._run_one(
        _config("ks"), conn, raw.parent, tmp_path / "cache", False, False,
    )
    assert result.verdict == "failed"
    assert "clean candidate" in result.error
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 1
