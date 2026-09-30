"""A slow state's live fetch is cut off by its wall-clock budget; the run goes on."""

from __future__ import annotations

import time
from dataclasses import replace

import pytest

from warnlive import pipeline
from warnlive.normalize.engine import NormalizeResult
from warnlive.registry import StateConfig, load_registry
from warnlive.store import db


def _config(postal: str, budget: float | None = None) -> StateConfig:
    return StateConfig(
        postal=postal, name="Test state", source="upstream", status="active",
        tier="easy", cadence="daily", needs_browser=False, min_rows=1,
        staleness_days=None, expected_columns=None,
        source_url="https://example.gov", notes="", fetch_budget_minutes=budget,
    )


def _record(state: str) -> dict:
    return {
        "state": state, "employer_name": "Acme", "location": "Town",
        "notice_date": "2026-01-01", "effective_date": None,
        "employees_affected": 20, "layoff_type": "closure",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": None,
        "raw_extra": "{}", "dedupe_key": f"{state}-key", "raw_record_hash": f"{state}-hash",
    }


def _hanging_fetch(tmp_path, slow: str, attempts: list):
    """A collector that retries forever and skips every failure, as GA's
    patch did on its flaky host."""
    def fetch_state(postal, data_dir, cache_dir):
        if postal != slow:
            raw = tmp_path / "raw" / f"{postal}.csv"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("company\nAcme\n")
            return raw
        while True:
            try:
                attempts.append(time.monotonic())
                time.sleep(0.05)
                raise ConnectionError("host timed out")
            except Exception:  # noqa: BLE001 — the skip-and-retry pattern
                continue
    return fetch_state


def test_slow_state_fails_with_a_clear_error_and_the_run_continues(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "test.sqlite")
    db.init_db(conn)
    attempts: list = []
    monkeypatch.setattr(pipeline.fetch, "fetch_state", _hanging_fetch(tmp_path, "ga", attempts))
    monkeypatch.setattr(
        "warnlive.migrate.source_bundle.validate_agency_raw_file", lambda *_: None,
    )
    monkeypatch.setattr(
        pipeline.engine, "normalize_file",
        lambda postal, *_, **__: NormalizeResult(
            state=postal.upper(), records=[_record(postal.upper())], raw_rows=1,
        ),
    )
    started = time.monotonic()
    # 0.01 minutes = 0.6 seconds.
    report = pipeline.run_states(
        conn, None, [_config("ga", budget=0.01), _config("ny")], tmp_path,
    )
    elapsed = time.monotonic() - started
    assert elapsed < 10
    ga, ny = report.outcomes
    assert ga.verdict == "failed"
    assert "0.01-minute wall-clock budget" in ga.error
    assert ga.error.startswith("FetchBudgetExceeded")
    fetch_check = next(c for c in ga.checks["checks"] if c["name"] == "fetch_ok")
    assert fetch_check["outcome"] == "fail"
    assert ny.verdict in {"ok", "degraded"}
    assert ny.new == 1
    rows = {r["state"]: r for r in conn.execute("SELECT state, verdict, error FROM state_runs")}
    assert rows["GA"]["verdict"] == "failed"
    assert "budget" in rows["GA"]["error"]
    assert conn.execute("SELECT COUNT(*) FROM notices WHERE state='GA'").fetchone()[0] == 0
    # The alarm is disarmed after the state: nothing fires during NY or later.
    time.sleep(0.8)


def test_budget_refires_when_a_collector_swallows_it(monkeypatch):
    monkeypatch.setattr(pipeline, "_BUDGET_REFIRE_SECONDS", 0.2)
    swallowed = []

    def stubborn():
        while True:
            try:
                time.sleep(0.05)
            except BaseException as exc:  # noqa: BLE001 — worst-case collector
                if not swallowed:
                    swallowed.append(exc)
                    continue
                raise

    started = time.monotonic()
    with pytest.raises(pipeline.FetchBudgetExceeded):
        with pipeline._wall_clock_budget(0.3):
            stubborn()
    assert len(swallowed) == 1
    assert time.monotonic() - started < 5


def test_default_and_override_budgets():
    assert pipeline.fetch_budget_minutes(_config("ct")) == pipeline.DEFAULT_FETCH_BUDGET_MINUTES
    assert pipeline.fetch_budget_minutes(_config("ks", budget=90)) == 90
    # The registry accepts the override and rejects nonsense.
    registry = load_registry()
    assert all(
        cfg.fetch_budget_minutes is None or cfg.fetch_budget_minutes > 0
        for cfg in registry.all()
    )


def test_registry_rejects_a_non_positive_budget(tmp_path):
    import yaml

    from warnlive.registry import REGISTRY_PATH

    raw = yaml.safe_load(REGISTRY_PATH.read_text())
    raw["ct"]["fetch_budget_minutes"] = 0
    path = tmp_path / "states.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="fetch_budget_minutes"):
        load_registry(path)


def test_cached_runs_are_not_budgeted(monkeypatch, tmp_path):
    raw = tmp_path / "raw" / "ct.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("company\nAcme\n")
    armed = []
    monkeypatch.setattr(pipeline, "_wall_clock_budget", lambda s: armed.append(s))
    monkeypatch.setattr(
        "warnlive.migrate.source_bundle.validate_agency_raw_file", lambda *_: None,
    )
    monkeypatch.setattr(
        pipeline.engine, "normalize_file",
        lambda *_, **__: NormalizeResult(state="CT", records=[_record("CT")], raw_rows=1),
    )
    outcome = pipeline._run_one(
        replace(_config("ct"), fetch_budget_minutes=1), None, raw.parent,
        tmp_path / "cache", True, True,
    )
    assert outcome.verdict != "failed"
    assert armed == []
