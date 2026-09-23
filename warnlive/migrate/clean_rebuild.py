"""Build an isolated, conservative candidate from the currently cached sources.

This intentionally is not a historical migration.  It never touches the
working database or downloads data.  Incompatible/ambiguous batches are
reported, not forced through verification.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from warnlive.fetch.custom.sc import cached_csv
from warnlive.normalize.engine import normalize_file
from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest
from warnlive.verify.harness import verify_state

POLICY = "clean-rebuild-v1"


def _ia_conflicts(records: list[dict]) -> set[str]:
    """Set aside whole same-key groups whose original rows disagree."""
    signatures: dict[str, set[str]] = defaultdict(set)
    for record in records:
        signatures[record["dedupe_key"]].add(record["raw_record_hash"])
    return {key for key, values in signatures.items() if len(values) > 1}


def _ga_idless_conflicts(records: list[dict]) -> set[str]:
    """Do not merge differing GA rows when the state supplied no filing ID."""
    idless = [record for record in records if not record.get("source_identity")]
    signatures: dict[str, set[str]] = defaultdict(set)
    for record in idless:
        signatures[record["dedupe_key"]].add(record["raw_record_hash"])
    return {key for key, values in signatures.items() if len(values) > 1}


def _ks_conflicts(records: list[dict]) -> set[str]:
    """Do not merge distinct source rows or invent a key for an ID-less row."""
    signatures: dict[str, set[str]] = defaultdict(set)
    missing = set()
    for record in records:
        key = record["dedupe_key"]
        signatures[key].add(record["raw_record_hash"])
        if not record.get("source_identity"):
            missing.add(key)
    return missing | {key for key, values in signatures.items() if len(values) > 1}


def _raw_exception(origin: str, reason: str, rec: dict) -> dict:
    """Keep the prepared-row pointer and source text for excluded raw data."""
    return {
        "origin": origin, "reason": reason, "state": rec.get("state"),
        "prepared_row": rec.get("prepared_row"),
        "dedupe_key": rec.get("dedupe_key"),
        "raw_record_hash": rec.get("raw_record_hash"),
        "source_row_sha256": rec.get("source_row_sha256"),
        "source_identity": rec.get("source_identity"),
        "source_notice_id": rec.get("source_notice_id"),
        "source_url": rec.get("source_url"),
        "raw_extra": rec.get("raw_extra"),
        "error": rec.get("error"),
    }


def build(
    db_path: Path, raw_dir: Path, sc_cache_dir: Path,
    states: set[str] | None = None,
    *, observed_at: str | None = None, exceptions: list[dict] | None = None,
) -> dict:
    db_path = Path(db_path)
    if db_path.exists():
        raise ValueError(f"candidate database already exists: {db_path}")
    raw_dir = Path(raw_dir)
    registry = load_registry()
    selected = {s.lower() for s in states} if states else None
    configs = [
        cfg for cfg in registry.all()
        if cfg.status in {"active", "archive"}
        and (selected is None or cfg.postal in selected)
    ]
    if selected and selected - {cfg.postal for cfg in configs}:
        raise ValueError(f"unknown or ineligible states: {sorted(selected - {c.postal for c in configs})}")
    if observed_at is not None:
        try:
            parsed = datetime.strptime(observed_at, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("observed_at must be a valid YYYY-MM-DD date") from exc
        if parsed.strftime("%Y-%m-%d") != observed_at:
            raise ValueError("observed_at must be a valid YYYY-MM-DD date")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    now = (
        f"{observed_at}T00:00:00Z" if observed_at is not None
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    report: dict = {"policy": POLICY, "database": str(db_path), "states": {}}
    with TemporaryDirectory(prefix="warn-clean-raw-") as scratch:
        sc_raw_dir = Path(scratch)
        conn = db_mod.connect(db_path)
        try:
            db_mod.init_db(conn)
            conn.execute(
                "INSERT INTO runs (started_at, finished_at, trigger) VALUES (?, ?, ?)",
                (now, now, POLICY),
            )
            conn.commit()
            for cfg in configs:
                postal = cfg.postal
                entry: dict = {}
                report["states"][postal.upper()] = entry
                source_dir = raw_dir
                source_path = raw_dir / f"{postal}.csv"
                origin = "cache/sc" if postal == "sc" else f"raw/{postal}.csv"
                try:
                    if postal == "sc":
                        source_path = sc_raw_dir / "sc.csv"
                        entry["cached_pdf_rows"] = cached_csv(sc_cache_dir, source_path)
                        source_dir = sc_raw_dir
                    if not source_path.is_file():
                        entry["status"] = "missing_raw"
                        if exceptions is not None:
                            exceptions.append({
                                "origin": origin, "state": postal.upper(),
                                "reason": "missing_source_file",
                            })
                        continue
                    norm = normalize_file(postal, source_dir, cfg.source_url)
                    entry.update(raw_rows=norm.raw_rows, parsed_rows=len(norm.records),
                                 parse_failures=norm.failed_rows)
                    if exceptions is not None:
                        exceptions.extend(
                            _raw_exception(origin, "parse_failure", failure)
                            for failure in norm.failures
                        )
                    if postal in {"ga", "ia", "ks"}:
                        conflicts = (
                            _ia_conflicts(norm.records) if postal == "ia"
                            else _ga_idless_conflicts(norm.records) if postal == "ga"
                            else _ks_conflicts(norm.records)
                        )
                        entry["quarantined_keys"] = len(conflicts)
                        entry["quarantined_rows"] = sum(
                            record["dedupe_key"] in conflicts for record in norm.records
                        )
                        if exceptions is not None:
                            exceptions.extend(
                                _raw_exception(
                                    origin,
                                    "missing_source_identity" if postal == "ks"
                                    and not record.get("source_identity")
                                    else "conflicting_same_key",
                                    record,
                                )
                                for record in norm.records
                                if record["dedupe_key"] in conflicts
                            )
                        norm = replace(norm, records=[
                            record for record in norm.records
                            if record["dedupe_key"] not in conflicts
                        ])
                    verification = verify_state(cfg, source_path, norm)
                    entry["verification"] = verification.to_dict()
                    if verification.verdict == "failed":
                        entry["status"] = "verification_failed"
                        if exceptions is not None:
                            exceptions.extend(
                                _raw_exception(origin, "verification_failed", record)
                                for record in norm.records
                            )
                        continue
                    stats = ingest(conn, norm.records, observed_at=now[:10])
                    entry.update(status="ingested", new=stats.new, updated=stats.updated,
                                 unchanged=stats.unchanged, coalesced=stats.coalesced,
                                 suspected_collisions=stats.suspected_collisions)
                    entry["unaccounted_rows"] = (
                        norm.raw_rows - norm.failed_rows
                        - entry.get("quarantined_rows", 0)
                        - stats.new - stats.updated - stats.unchanged
                        - stats.coalesced
                    )
                except Exception as exc:  # one source cannot invalidate other candidates
                    conn.rollback()
                    entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
                    if exceptions is not None:
                        exceptions.append({
                            "origin": origin, "state": postal.upper(),
                            "reason": "state_error", "error": entry["error"],
                        })
            report["total_notices"] = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
            report["total_versions"] = conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0]
            report["total_workers"] = conn.execute(
                "SELECT COALESCE(SUM(employees_affected), 0) FROM notices"
            ).fetchone()[0]
            report["integrity"] = conn.execute("PRAGMA integrity_check").fetchone()[0]
            report["foreign_key_errors"] = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            conn.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, default=Path("workdir/raw"))
    parser.add_argument("--sc-cache-dir", type=Path, default=Path("workdir/cache/sc"))
    parser.add_argument("--states", nargs="*")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--observed-at", help="Pin YYYY-MM-DD for repeatable observations")
    args = parser.parse_args()
    result = build(args.db, args.raw_dir, args.sc_cache_dir,
                   set(args.states) if args.states else None,
                   observed_at=args.observed_at)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "total_notices": result["total_notices"],
        "total_versions": result["total_versions"],
        "states": {state: entry["status"] for state, entry in result["states"].items()},
    }, sort_keys=True))
