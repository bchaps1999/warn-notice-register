"""Orchestrate fetch -> normalize -> verify -> ingest for a set of states.

One state failing never fails the run; every state's outcome is recorded in
state_runs and surfaced in the health report.
"""

from __future__ import annotations

import json
import hashlib
import logging
import sqlite3
import traceback
from dataclasses import dataclass, field
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from warnlive import fetch
from warnlive.normalize import engine
from warnlive.normalize.engine import _dedupe_key
from warnlive.normalize.admission import (
    exclusion_reasons, ks_ambiguity_reasons,
    ks_event_signature,
    load_ks_hold_policy, persist_ks_hold_policy,
)
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
    suspected_collisions: int = 0
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
    durable_policy_path: Path = Path("data/ks_portal_holds.json"),
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
        try:
            if conn is not None and not smoke:
                # An outer transaction is essential: releasing the state
                # savepoint alone commits in SQLite when no BEGIN exists.
                conn.execute("BEGIN")
            outcome = _run_one(
                cfg, conn, data_dir, cache_dir, smoke, use_cache, trigger,
                commit_state=False, durable_policy_path=durable_policy_path,
            )
        except Exception as e:  # noqa: BLE001 — one broken state never ends the run
            if conn is not None and not smoke:
                conn.rollback()
            outcome = StateOutcome(
                state=cfg.postal.upper(), verdict="failed",
                error=f"state: {type(e).__name__}: {e}",
            )
            logger.exception("state %s failed", cfg.postal)
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
            try:
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
            except Exception as e:  # noqa: BLE001 — state data must not outlive telemetry
                conn.rollback()
                outcome.verdict = "failed"
                outcome.error = f"state log: {type(e).__name__}: {e}"
                outcome.new = outcome.updated = outcome.unchanged = 0
                logger.exception("state log %s failed", cfg.postal)

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
    trigger: str = "manual",
    commit_state: bool = True,
    durable_policy_path: Path = Path("data/ks_portal_holds.json"),
) -> StateOutcome:
    postal = cfg.postal
    outcome = StateOutcome(state=postal.upper(), verdict="failed")
    raw_path: Path | None = None
    fetch_error: str | None = None
    fetched_live = False

    expected_raw = data_dir / f"{postal}.csv"
    try:
        if use_cache and expected_raw.exists():
            raw_path = expected_raw
        else:
            raw_path = fetch.fetch_state(postal, data_dir, cache_dir)
            fetched_live = True
    except Exception as e:  # noqa: BLE001 — a state must never kill the run
        fetch_error = f"{type(e).__name__}: {e}"
        outcome.error = fetch_error
        logger.debug("fetch %s failed:\n%s", postal, traceback.format_exc())

    norm = None
    if raw_path is not None:
        try:
            from warnlive.migrate.source_bundle import validate_agency_raw_file

            validate_agency_raw_file(postal, raw_path)
            norm = engine.normalize_file(postal, raw_path.parent, cfg.source_url)
            outcome.raw_rows = norm.raw_rows
            outcome.normalized_rows = len(norm.records)
        except Exception as e:  # noqa: BLE001
            outcome.error = f"normalize: {type(e).__name__}: {e}"
            logger.debug("normalize %s failed:\n%s", postal, traceback.format_exc())

    excluded: list[dict] = []
    if norm is not None:
        reasons = exclusion_reasons(postal, norm.records)
        if postal == "ks":
            try:
                durable_path = Path(durable_policy_path)
                held_ids, held_signatures = load_ks_hold_policy()
                if durable_path.exists():
                    durable_ids, durable_signatures = load_ks_hold_policy(
                        durable_path
                    )
                    held_ids |= durable_ids
                    held_signatures |= durable_signatures
                current_ids: set[str] = set()
                current_signatures: set[tuple[str, str]] = set()
                current_path = data_dir / "ks.hold_policy.json"
                if fetched_live and cfg.source == "custom" and not current_path.is_file():
                    raise ValueError("complete Kansas collector omitted current hold policy")
                if current_path.is_file():
                    current = json.loads(current_path.read_text())
                    raw_digest = hashlib.sha256(raw_path.read_bytes()).hexdigest()
                    if current.get("raw_sha256") != raw_digest:
                        raise ValueError("Kansas current hold policy does not match raw CSV")
                    current_ids, current_signatures = load_ks_hold_policy(current_path)
                    held_ids |= current_ids
                    held_signatures |= current_signatures
                    if fetched_live and not smoke:
                        held_ids, held_signatures = persist_ks_hold_policy(
                            current_path, durable_path
                        )
                existing = []
                if conn is not None:
                    existing = [dict(row) for row in conn.execute(
                        "SELECT source_identity, employer_name, notice_date "
                        "FROM notices WHERE state = 'KS'"
                    )]
                conflicts = sorted(
                    row["source_identity"] or "(missing source ID)" for row in existing
                    if row["source_identity"] in held_ids
                    or ks_event_signature(
                        row["employer_name"], row["notice_date"]
                    ) in held_signatures
                )
                if conflicts:
                    outcome.error = (
                        "Kansas portal holds existing canonical IDs: "
                        + ", ".join(conflicts[:12])
                    )
                    outcome.checks = {"verdict": "failed", "admission": {
                        "publication_blocked": True,
                        "reason": "held_event_already_published",
                        "source_identities": conflicts,
                    }}
                    return outcome
                for key, reason in ks_ambiguity_reasons(
                    norm.records, existing, held_ids, held_signatures
                ).items():
                    reasons.setdefault(key, reason)
            except (OSError, ValueError) as e:
                outcome.error = f"Kansas admission policy: {e}"
                outcome.checks = {"verdict": "failed", "admission": outcome.error}
                return outcome
        excluded = [
            {
                "reason": reasons[record["dedupe_key"]],
                "prepared_row": record.get("prepared_row"),
                "dedupe_key": record["dedupe_key"],
                "raw_record_hash": record.get("raw_record_hash"),
                "source_url": record.get("source_url"),
                "raw_extra": record.get("raw_extra"),
            }
            for record in norm.records if record["dedupe_key"] in reasons
        ]
        norm = replace(norm, records=[
            record for record in norm.records if record["dedupe_key"] not in reasons
        ])
    verification = harness.verify_state(cfg, raw_path, norm, fetch_error=fetch_error)
    outcome.checks = verification.to_dict()
    if norm is not None:
        outcome.checks["admission"] = {
            "eligible_rows": len(norm.records),
            "excluded_rows": len(excluded) + norm.failed_rows,
            "exclusions": excluded + [
                {"reason": "parse_failure", **failure}
                for failure in norm.failures
            ],
        }
    outcome.verdict = verification.verdict

    # failed runs never ingest; degraded runs do (warn-level findings only)
    if conn is not None and not smoke and norm is not None and outcome.verdict != "failed":
        if postal == "il":
            # A generic clean-rebuild marker is insufficient: earlier clean
            # candidates still used mutable employer/date/place keys for IL.
            # Refuse a mixed database before any notice is written.
            existing = conn.execute(
                "SELECT dedupe_key, source_identity FROM notices WHERE state = 'IL'"
            ).fetchall()
            identities = [row["source_identity"] for row in existing]
            compliant = all(
                identity and identity.startswith("IL:IEBS:")
                and identity != "IL:IEBS:"
                and row["dedupe_key"] == _dedupe_key({
                    "state": "IL", "source_identity": identity,
                })
                for row, identity in zip(existing, identities)
            ) and len(identities) == len(set(identities))
            if not compliant:
                outcome.verdict = "failed"
                outcome.error = (
                    "IL source-record keys require a clean candidate or reviewed "
                    "migration before ingest into this database"
                )
                outcome.checks["verdict"] = "failed"
                outcome.checks["migration"] = outcome.error
                return outcome
        # Source-identity keys are intentionally enabled for fresh builds, but
        # the currently published DB still holds GA/SC/KS rows under legacy keys.
        # Do not mix both policies in one DB: that would duplicate filings.
        if postal in {"ga", "sc", "ks"} and conn.execute(
            "SELECT 1 FROM notices WHERE state = ? LIMIT 1", (postal.upper(),)
        ).fetchone() and not conn.execute(
            "SELECT 1 FROM runs WHERE trigger = 'clean-rebuild-v1' LIMIT 1"
        ).fetchone():
            outcome.verdict = "failed"
            outcome.error = (
                f"{postal.upper()} source-identity keys require a clean candidate "
                "or reviewed migration before ingest into this database"
            )
            outcome.checks["verdict"] = "failed"
            outcome.checks["migration"] = outcome.error
            return outcome
        if postal == "sc" and conn.execute(
            "SELECT 1 FROM notices WHERE state = 'SC' AND source_details IS NULL LIMIT 1"
        ).fetchone():
            outcome.verdict = "failed"
            outcome.error = (
                "SC source-date correction changes existing notice identities; "
                "run a reviewed migration before ingest"
            )
            outcome.checks["verdict"] = "failed"
            outcome.checks["migration"] = outcome.error
            return outcome
        # The previous successful run's date, read before this run is
        # recorded: it is the last date an absent notice was actually seen,
        # which is what freeze_absent stamps on it.
        prev = conn.execute(
            "SELECT MAX(substr(finished_at, 1, 10)) AS d FROM state_runs "
            "WHERE state = ? AND verdict IN ('ok', 'degraded')",
            (postal.upper(),),
        ).fetchone()
        try:
            conn.execute("SAVEPOINT state_ingest")
            records = norm.records
            stats = dedupe.ingest(conn, records, observed_at=now_utc()[:10], commit=False)
            # Absence is evidence only from a complete, current source snapshot.
            # Cache runs, backfills, and partly unparseable files cannot support it.
            complete_live = (
                fetched_live and norm.failed_rows == 0 and not excluded
                and trigger != "backfill" and postal not in {"ga", "sc", "ks"}
            )
            if complete_live:
                dedupe.freeze_absent(
                    conn, postal,
                    {r["dedupe_key"] for r in records},
                    (prev and prev["d"]) or now_utc()[:10],
                    commit=False,
                )
            conn.execute("RELEASE SAVEPOINT state_ingest")
            if commit_state:
                conn.commit()
            outcome.new, outcome.updated, outcome.unchanged = stats.new, stats.updated, stats.unchanged
            outcome.suspected_collisions = stats.suspected_collisions
            outcome.checks["ingest"] = {
                "suspected_collisions": stats.suspected_collisions,
                "absence_frozen": complete_live,
            }
        except Exception as e:  # noqa: BLE001 — state writes must be all-or-nothing
            conn.execute("ROLLBACK TO SAVEPOINT state_ingest")
            conn.execute("RELEASE SAVEPOINT state_ingest")
            conn.rollback()
            outcome.verdict = "failed"
            outcome.error = f"ingest: {type(e).__name__}: {e}"
            outcome.checks["verdict"] = "failed"
            outcome.checks["ingest"] = {"error": outcome.error}
            if isinstance(e, dedupe.CollisionError):
                outcome.checks["identity"] = {
                    "status": "failed",
                    "reason": "suspected_same_key_collision",
                    "collision_window_days": dedupe.COLLISION_WINDOW_DAYS,
                    "collisions": e.collisions,
                }
            logger.exception("ingest %s failed", postal)

    return outcome
