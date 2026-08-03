"""Orchestrate fetch -> normalize -> verify -> ingest for a set of states.

One state failing never fails the run; every state's outcome is recorded in
state_runs and surfaced in the health report.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from warnlive import fetch
from warnlive.normalize import engine
from warnlive.registry import Registry, StateConfig
from warnlive.store import dedupe
from warnlive.verify import harness

logger = logging.getLogger("warnlive")


@dataclass
class StateOutcome:
    state: str
    verdict: str  # ok | degraded | failed | skipped
    raw_rows: int = 0
    normalized_rows: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    checks: dict | None = None
    error: str | None = None


@dataclass
class RunReport:
    trigger: str
    started_at: str
    outcomes: list[StateOutcome] = field(default_factory=list)


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_states(
    conn: sqlite3.Connection | None,
    registry: Registry,
    configs: list[StateConfig],
    workdir: Path,
    trigger: str = "manual",
    smoke: bool = False,
    use_cache: bool = False,
) -> RunReport:
    """Run the pipeline for each state config.

    smoke: fetch + normalize + verify only; nothing is written to the DB.
    use_cache: skip the live fetch when a raw CSV from a prior run exists
    (for iterating on normalization without hammering state sites).
    """
    workdir = Path(workdir)
    data_dir = workdir / "raw"
    cache_dir = workdir / "cache"
    report = RunReport(trigger=trigger, started_at=now_utc())

    run_id = None
    if conn is not None and not smoke:
        cur = conn.execute(
            "INSERT INTO runs (started_at, trigger) VALUES (?, ?)",
            (report.started_at, trigger),
        )
        run_id = cur.lastrowid
        conn.commit()

    for cfg in configs:
        outcome = _run_one(cfg, conn, data_dir, cache_dir, smoke, use_cache)
        report.outcomes.append(outcome)
        logger.info(
            "%s: %s (raw=%d normalized=%d new=%d updated=%d)%s",
            outcome.state,
            outcome.verdict,
            outcome.raw_rows,
            outcome.normalized_rows,
            outcome.new,
            outcome.updated,
            f" error={outcome.error}" if outcome.error else "",
        )
        if conn is not None and not smoke:
            conn.execute(
                """INSERT INTO state_runs
                   (run_id, state, verdict, raw_rows, normalized_rows,
                    new_notices, updated_notices, checks_json, error, finished_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id,
                    outcome.state,
                    outcome.verdict,
                    outcome.raw_rows,
                    outcome.normalized_rows,
                    outcome.new,
                    outcome.updated,
                    json.dumps(outcome.checks) if outcome.checks else None,
                    outcome.error,
                    now_utc(),
                ),
            )
            conn.commit()

    if conn is not None and not smoke:
        conn.execute("UPDATE runs SET finished_at = ? WHERE id = ?", (now_utc(), run_id))
        conn.commit()
    return report


def _run_one(
    cfg: StateConfig,
    conn: sqlite3.Connection | None,
    data_dir: Path,
    cache_dir: Path,
    smoke: bool,
    use_cache: bool,
) -> StateOutcome:
    postal = cfg.postal
    outcome = StateOutcome(state=postal.upper(), verdict="failed")
    raw_path: Path | None = None
    fetch_error: str | None = None

    expected_raw = data_dir / f"{postal}.csv"
    try:
        if use_cache and expected_raw.exists():
            raw_path = expected_raw
        else:
            raw_path = fetch.fetch_state(postal, data_dir, cache_dir)
    except Exception as e:  # noqa: BLE001 — a state must never kill the run
        fetch_error = f"{type(e).__name__}: {e}"
        outcome.error = fetch_error
        logger.debug("fetch %s failed:\n%s", postal, traceback.format_exc())

    norm = None
    if raw_path is not None:
        try:
            norm = engine.normalize_file(postal, raw_path.parent, cfg.source_url)
            outcome.raw_rows = norm.raw_rows
            outcome.normalized_rows = len(norm.records)
        except Exception as e:  # noqa: BLE001
            outcome.error = f"normalize: {type(e).__name__}: {e}"
            logger.debug("normalize %s failed:\n%s", postal, traceback.format_exc())

    verification = harness.verify_state(cfg, raw_path, norm, fetch_error=fetch_error)
    outcome.checks = verification.to_dict()
    outcome.verdict = verification.verdict

    # failed runs never ingest; degraded runs do (warn-level findings only)
    if conn is not None and not smoke and norm is not None and outcome.verdict != "failed":
        # The previous successful run's date, read before this run is
        # recorded: it is the last date an absent notice was actually seen,
        # which is what freeze_absent stamps on it.
        prev = conn.execute(
            "SELECT MAX(substr(finished_at, 1, 10)) AS d FROM state_runs "
            "WHERE state = ? AND verdict IN ('ok', 'degraded')",
            (postal.upper(),),
        ).fetchone()
        stats = dedupe.ingest(conn, norm.records, observed_at=now_utc()[:10])
        outcome.new, outcome.updated, outcome.unchanged = stats.new, stats.updated, stats.unchanged
        # A live fetch is the current state of the source, so what it does
        # not mention has left it. Backfills never come through here.
        dedupe.freeze_absent(
            conn, postal,
            {r["dedupe_key"] for r in norm.records},
            (prev and prev["d"]) or now_utc()[:10],
        )

    return outcome
