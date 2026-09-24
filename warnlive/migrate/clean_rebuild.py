"""Build an isolated, conservative candidate from the currently cached sources.

This intentionally is not a historical migration.  It never touches the
working database or downloads data.  Incompatible/ambiguous batches are
reported, not forced through verification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from warnlive.fetch.custom.sc import cached_csv
from warnlive.normalize.engine import normalize_file
from warnlive.normalize.admission import exclusion_reasons
from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.dedupe import CollisionError, ingest, preflight_collisions
from warnlive.verify.harness import verify_state

POLICY = "clean-rebuild-v1"


def _raw_exception(origin: str, reason: str, rec: dict) -> dict:
    """Keep the prepared-row pointer and source text for excluded raw data."""
    notice_date = rec.get("notice_date")
    raw_extra = rec.get("raw_extra")
    source_row_sha256 = rec.get("source_row_sha256")
    if source_row_sha256 is None and isinstance(raw_extra, str):
        source_row_sha256 = hashlib.sha256(raw_extra.encode()).hexdigest()
    return {
        "origin": origin, "reason": reason, "state": rec.get("state"),
        "notice_year": notice_date[:4] if isinstance(notice_date, str)
        and len(notice_date) >= 5 and notice_date[:4].isdigit()
        and notice_date[4] == "-" else None,
        "prepared_row": rec.get("prepared_row"),
        "dedupe_key": rec.get("dedupe_key"),
        "raw_record_hash": rec.get("raw_record_hash"),
        "source_row_sha256": source_row_sha256,
        "source_identity": rec.get("source_identity"),
        "source_notice_id": rec.get("source_notice_id"),
        "source_url": rec.get("source_url"),
        "raw_extra": raw_extra,
        "error": rec.get("error"),
    }


def _coalesced_observations(origin: str, records: list[dict]) -> list[dict]:
    """Identify rows ingest will collapse under the same key/content hash."""
    previous_by_key: dict[str, dict] = {}
    excluded = []
    for rec in records:
        prior = previous_by_key.get(rec["dedupe_key"])
        if prior and prior["raw_record_hash"] == rec["raw_record_hash"]:
            item = _raw_exception(origin, "coalesced_same_key_content", rec)
            item.update({
                "match_basis": "canonical_key_and_content",
                "survivor_prepared_row": prior.get("prepared_row"),
            })
            excluded.append(item)
        else:
            previous_by_key[rec["dedupe_key"]] = rec
    return excluded


def _quarantine_date_collisions(conn, origin: str, records: list[dict]) -> tuple[list[dict], list[dict], int]:
    """Hold an entire ambiguous key group with pointers to every source row."""
    try:
        preflight_collisions(conn, records)
    except CollisionError as exc:
        keys = {item["dedupe_key"] for item in exc.collisions}
        held = []
        for rec in records:
            if rec["dedupe_key"] in keys:
                item = _raw_exception(origin, "suspected_same_key_collision", rec)
                item["date_conflicts"] = [
                    conflict for conflict in exc.collisions
                    if conflict["dedupe_key"] == rec["dedupe_key"]
                ]
                held.append(item)
        return [rec for rec in records if rec["dedupe_key"] not in keys], held, len(keys)
    return records, [], 0


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
                pending_exceptions: list[dict] = []
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
                            pending_exceptions.append({
                                "origin": origin, "state": postal.upper(),
                                "reason": "missing_source_file",
                            })
                            exceptions.extend(pending_exceptions)
                        continue
                    from warnlive.migrate.source_bundle import validate_agency_raw_file

                    validate_agency_raw_file(postal, source_path)
                    norm = normalize_file(postal, source_dir, cfg.source_url)
                    entry.update(raw_rows=norm.raw_rows, parsed_rows=len(norm.records),
                                 parse_failures=norm.failed_rows)
                    if exceptions is not None:
                        pending_exceptions.extend(
                            _raw_exception(origin, "parse_failure", failure)
                            for failure in norm.failures
                        )
                    if postal in {"ga", "ia", "il", "ks", "nj"}:
                        reasons = exclusion_reasons(postal, norm.records)
                        conflicts = set(reasons)
                        entry["quarantined_keys"] = len(conflicts)
                        entry["quarantined_rows"] = sum(
                            record["dedupe_key"] in conflicts for record in norm.records
                        )
                        if exceptions is not None:
                            pending_exceptions.extend(
                                _raw_exception(
                                    origin,
                                    reasons[record["dedupe_key"]],
                                    record,
                                )
                                for record in norm.records
                                if record["dedupe_key"] in conflicts
                            )
                        norm = replace(norm, records=[
                            record for record in norm.records
                            if record["dedupe_key"] not in conflicts
                        ])
                    safe_records, collision_holds, collision_keys = _quarantine_date_collisions(
                        conn, origin, norm.records,
                    )
                    if collision_keys:
                        entry["quarantined_keys"] = entry.get("quarantined_keys", 0) + collision_keys
                        entry["quarantined_rows"] = entry.get("quarantined_rows", 0) + len(collision_holds)
                        pending_exceptions.extend(collision_holds)
                        norm = replace(norm, records=safe_records)
                    verification = verify_state(cfg, source_path, norm)
                    entry["verification"] = verification.to_dict()
                    if verification.verdict == "failed":
                        entry["status"] = "verification_failed"
                        if exceptions is not None:
                            pending_exceptions.extend(
                                _raw_exception(origin, "verification_failed", record)
                                for record in norm.records
                            )
                            exceptions.extend(pending_exceptions)
                        continue
                    if exceptions is not None:
                        pending_exceptions.extend(_coalesced_observations(origin, norm.records))
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
                    if exceptions is not None:
                        exceptions.extend(pending_exceptions)
                except Exception as exc:  # one source cannot invalidate other candidates
                    conn.rollback()
                    entry.update(status="error", error=f"{type(exc).__name__}: {exc}")
                    if exceptions is not None:
                        # A source-only release cannot account for a failed batch:
                        # the transaction may have removed proposed survivors.
                        # Discard staged dispositions and stop the candidate.
                        raise RuntimeError(
                            f"source-only state {postal.upper()} failed: {entry['error']}"
                        ) from exc
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
