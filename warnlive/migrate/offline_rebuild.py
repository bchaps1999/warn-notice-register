"""Rebuild an isolated WARN candidate from a frozen local source bundle.

No network requests are made.  The bundled overlap policy is a transitional
record of which historical keys were accepted before this rebuild; it keeps
overlapping BLN/archive inputs from silently creating extra notices.  A
candidate is never promoted or exported by this command.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import sqlite3
import urllib.request
from collections import defaultdict
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from warnlive.backfill import bln_integrated, state_archives
from warnlive.enrich import il_effective
from warnlive.migrate.clean_rebuild import build as build_raw
from warnlive.migrate.source_bundle import extract
from warnlive.normalize.engine import _record_hash, normalize_file
from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store import links as links_mod
from warnlive.store.dedupe import ingest

logger = logging.getLogger("warnlive")


def _read_policy(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("bundle has no rebuild_policy.json; recreate it with --policy-db")
    policy = json.loads(path.read_text())
    if policy.get("format") != "warn-rebuild-policy-v1":
        raise ValueError("unsupported rebuild overlap policy")
    return policy


def _ingest_groups(conn, groups: dict[str, list[dict]], observed_at: str) -> dict:
    added = updated = collisions = 0
    for state, records in sorted(groups.items()):
        if not records:
            continue
        stats = ingest(conn, records, observed_at=observed_at)
        added += stats.new
        updated += stats.updated
        collisions += stats.suspected_collisions
    return {"new": added, "updated": updated, "suspected_collisions": collisions}


def _bln_conservative(conn, source: Path, observed_at: str) -> dict:
    registry = load_registry()
    allowed = [cfg.postal for cfg in registry.all() if cfg.postal not in {"ga", "sc"}]
    report = {}
    for label, select in (
        ("older", bln_integrated.older_rows_by_state),
        ("empty_months", bln_integrated.gap_rows_by_state),
    ):
        groups = select(source, conn, registry, states=allowed)
        report[label] = {"input_rows": sum(map(len, groups.values())),
                         **_ingest_groups(conn, groups, observed_at)}
    return report


def _backfill_raw(conn, raw_dir: Path, observed_at: str) -> dict:
    registry = load_registry()
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    report = {"files": 0, "raw_rows": 0, "parse_failures": 0,
              "skipped_existing": 0, "quarantined_conflicts": 0}
    groups_by_state: dict[str, list[dict]] = defaultdict(list)
    for source in sorted(raw_dir.glob("*.csv")):
        postal = source.stem
        if postal in {"ga", "sc"} or postal not in registry:
            continue
        norm = normalize_file(postal, raw_dir, registry[postal].source_url)
        report["files"] += 1
        report["raw_rows"] += norm.raw_rows
        report["parse_failures"] += norm.failed_rows
        by_key: dict[str, list[dict]] = defaultdict(list)
        for rec in norm.records:
            by_key[rec["dedupe_key"]].append(rec)
        for key, rows in by_key.items():
            if key in seen:
                report["skipped_existing"] += len(rows)
            elif len({row["raw_record_hash"] for row in rows}) > 1:
                report["quarantined_conflicts"] += len(rows)
            else:
                groups_by_state[postal.upper()].append(rows[0])
                seen.add(key)
    return {**report, **_ingest_groups(conn, groups_by_state, observed_at)}


def _cached_only(_url: str, dest: Path) -> bytes | None:
    return dest.read_bytes() if dest.is_file() else None


def _cached_ny(cache: Path) -> list[dict]:
    ids = sorted((p.stem for p in (cache / "archives/ny").glob("*.html")), key=int)
    rows = [["urlkey", "timestamp", "original"]] + [
        ["", "20150101000000", f"http://labor.ny.gov/app/warn/details.asp?id={ident}"]
        for ident in ids
    ]

    def cached_index(_request, timeout=None):
        return io.BytesIO(json.dumps(rows).encode())

    with patch.object(urllib.request, "urlopen", side_effect=cached_index), patch.object(
        state_archives, "_download", side_effect=_cached_only
    ):
        return state_archives.fetch_ny(cache)


def _cached_agencies(
    conn, cache: Path, accepted: set[str], source_urls: dict[str, str],
    observed_at: str,
) -> dict:
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    report = {}
    for state in ("WI", "FL", "CA", "MA", "OH", "NY"):
        if state == "NY":
            records = _cached_ny(cache)
        else:
            captures = ["cached://annual-pdf"] if state == "CA" else []
            with patch.object(state_archives, "_wayback_captures", return_value=captures), patch.object(
                state_archives, "_download", side_effect=_cached_only
            ):
                records = state_archives.FETCHERS[state](cache)
        by_key: dict[str, list[dict]] = defaultdict(list)
        for rec in records:
            by_key[rec["dedupe_key"]].append(rec)
        eligible = []
        skipped = ambiguous = outside_policy = 0
        for key, rows in by_key.items():
            if key in seen:
                skipped += len(rows)
            elif key not in accepted:
                outside_policy += len(rows)
            elif len({row["raw_record_hash"] for row in rows}) > 1:
                ambiguous += len(rows)
            else:
                rec = rows[0]
                if key in source_urls:
                    rec["source_url"] = source_urls[key]
                    rec["raw_record_hash"] = _record_hash(rec)
                eligible.append(rec)
                seen.add(key)
        stats = _ingest_groups(conn, {state: eligible}, observed_at)
        report[state] = {"parsed": len(records), "already": skipped,
                         "outside_policy": outside_policy,
                         "ambiguous_rows": ambiguous, **stats}
    return report


def _bln_accepted(conn, source: Path, accepted: set[str], observed_at: str) -> dict:
    registry = load_registry()
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    by_key: dict[str, list[dict]] = defaultdict(list)
    eligible_rows = 0
    with source.open(newline="") as fh:
        for row in csv.DictReader(fh):
            state = (row.get("postal_code") or "").upper()
            if (state in {"GA", "SC", "IA"} or state.lower() not in registry
                    or row.get("is_superseded") == "True"):
                continue
            try:
                rec = bln_integrated.to_canonical(row, registry[state.lower()].source_url)
            except (ValueError, KeyError, TypeError):
                continue
            if not rec["employer_name"]:
                continue
            eligible_rows += 1
            if rec["dedupe_key"] in accepted and rec["dedupe_key"] not in seen:
                by_key[rec["dedupe_key"]].append(rec)
    groups: dict[str, list[dict]] = defaultdict(list)
    ambiguous = 0
    for rows in by_key.values():
        if len({row["raw_record_hash"] for row in rows}) > 1:
            ambiguous += len(rows)
        else:
            groups[rows[0]["state"]].append(rows[0])
    return {"eligible_integrated_rows": eligible_rows,
            "ambiguous_rows": ambiguous,
            **_ingest_groups(conn, groups, observed_at)}


def _metrics(conn) -> dict:
    rows = conn.execute(
        "SELECT state, COUNT(*) notices, COALESCE(SUM(employees_affected),0) workers, "
        "SUM(notice_date IS NULL) missing_notice_dates, "
        "SUM(effective_date IS NULL) missing_effective_dates, "
        "SUM(location IS NULL OR trim(location)='') missing_locations "
        "FROM notices GROUP BY state"
    )
    return {row["state"]: dict(row) for row in rows}


_FINGERPRINT_QUERIES = {
    "notices": (
        "SELECT dedupe_key,state,employer_name,location,notice_date,effective_date,"
        "employees_affected,layoff_type,is_temporary,is_amendment,source_url,"
        "source_notice_id,is_amended,current_version,site_address,effective_date_end,"
        "notice_date_precision,notice_date_basis,source_identity,source_details "
        "FROM notices ORDER BY dedupe_key"
    ),
    "versions": (
        "SELECT n.dedupe_key,v.version,v.raw_record_hash,v.fields_json "
        "FROM notice_versions v JOIN notices n ON n.id=v.notice_id "
        "ORDER BY n.dedupe_key,v.version"
    ),
    "links": (
        "SELECT a.dedupe_key,b.dedupe_key,l.kind,l.score,l.method,l.detail "
        "FROM notice_links l JOIN notices a ON a.id=l.notice_id "
        "JOIN notices b ON b.id=l.related_id ORDER BY 1,2,3,4,5,6"
    ),
}


def _fingerprints(conn) -> dict[str, str]:
    """Hash stable content, excluding database IDs and observation timestamps."""
    result = {}
    for name, query in _FINGERPRINT_QUERIES.items():
        digest = hashlib.sha256()
        for row in conn.execute(query):
            digest.update(json.dumps(tuple(row), separators=(",", ":"),
                                     ensure_ascii=False).encode())
            digest.update(b"\n")
        result[name] = digest.hexdigest()
    return result


def _repair_il_from_cache(db: Path, cache: Path) -> dict:
    """Replay the existing IL date repair using only frozen monthly reports."""
    if not cache.is_dir():
        return {"cached_files": 0, "parsed_records": 0, "failed_files": [],
                "result": "skipped: no frozen IL report cache"}
    files = sorted(p for p in cache.iterdir() if p.suffix.lower() in {".pdf", ".xlsx"})
    records = []
    failed = []
    for path in files:
        try:
            records.extend(il_effective.parse_report(path))
        except Exception as exc:  # A malformed cached report must remain visible.
            failed.append({"file": path.name, "error": str(exc)})
    from warnlive.cli import il_effective_dates

    output = io.StringIO()
    with patch.object(il_effective, "collect_records", return_value=records), redirect_stdout(output):
        il_effective_dates.callback("", cache.parent.parent, db, False)
    return {"cached_files": len(files), "parsed_records": len(records),
            "failed_files": failed, "result": output.getvalue().strip()}


def rebuild(bundle: Path, out_db: Path, observed_at: str, compare_db: Path | None = None) -> dict:
    if out_db.exists():
        raise FileExistsError(f"candidate database already exists: {out_db}")
    with TemporaryDirectory(prefix="warn-rebuild-sources-") as temp:
        source = Path(temp) / "sources"
        manifest = extract(bundle, source)
        policy = _read_policy(source / "rebuild_policy.json")
        accepted = set(policy["accepted_keys"])
        raw_report = build_raw(out_db, source / "raw", source / "cache/sc")
        conn = db_mod.connect(out_db)
        try:
            backfill = _bln_conservative(
                conn, source / "backfill/bln_integrated.csv", observed_at
            )
            backfill_raw = _backfill_raw(conn, source / "backfill/raw", observed_at)
            agencies = _cached_agencies(
                conn, source / "backfill/cache", accepted,
                policy["archive_source_urls"], observed_at,
            )
            accepted_bln = _bln_accepted(
                conn, source / "backfill/bln_integrated.csv", accepted, observed_at,
            )
            conn.commit()
            il_repair = _repair_il_from_cache(out_db, source / "cache/il_reports")
            link_report = links_mod.rebuild(conn)
            report = {
                "source_files": len(manifest["files"]),
                "source_bytes": sum(item["size"] for item in manifest["files"]),
                "policy": policy["format"], "raw": raw_report["states"],
                "bln_conservative": backfill, "backfill_raw": backfill_raw,
                "cached_agencies": agencies, "bln_accepted": accepted_bln,
                "il_effective_repair": il_repair,
                "link_rebuild": link_report,
                "fingerprints": _fingerprints(conn),
                "states": _metrics(conn),
                "notices": conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0],
                "versions": conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0],
                "links": conn.execute("SELECT COUNT(*) FROM notice_links").fetchone()[0],
                "workers": conn.execute(
                    "SELECT COALESCE(SUM(employees_affected),0) FROM notices"
                ).fetchone()[0],
                "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0],
                "foreign_key_errors": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
            }
        finally:
            conn.close()
        if compare_db is not None:
            if not compare_db.is_file():
                raise FileNotFoundError(f"comparison database missing: {compare_db}")
            baseline = sqlite3.connect(f"file:{compare_db.resolve()}?mode=ro", uri=True)
            baseline.row_factory = sqlite3.Row
            try:
                baseline.execute(
                    "ATTACH DATABASE ? AS candidate", (f"file:{out_db.resolve()}?mode=ro",)
                )
                report["baseline_states"] = _metrics(baseline)
                report["baseline_notices"] = sum(
                    item["notices"] for item in report["baseline_states"].values()
                )
                report["baseline_workers"] = sum(
                    item["workers"] for item in report["baseline_states"].values()
                )
                report["baseline_versions"] = baseline.execute(
                    "SELECT COUNT(*) FROM main.notice_versions"
                ).fetchone()[0]
                report["baseline_links"] = baseline.execute(
                    "SELECT COUNT(*) FROM main.notice_links"
                ).fetchone()[0]
                row = baseline.execute("""
                    SELECT COUNT(*) matched,
                           SUM(m.notice_date IS NOT c.notice_date) notice_date_changes,
                           SUM(m.effective_date IS NOT c.effective_date) effective_date_changes,
                           SUM(m.location IS NOT c.location) location_changes,
                           SUM(m.employees_affected IS NOT c.employees_affected) worker_changes,
                           SUM(m.source_url IS NOT c.source_url) source_url_changes
                    FROM main.notices m JOIN candidate.notices c USING(dedupe_key)
                """).fetchone()
                report["same_key_comparison"] = dict(row)
                report["baseline_keys_missing"] = baseline.execute("""
                    SELECT COUNT(*) FROM main.notices m WHERE NOT EXISTS
                    (SELECT 1 FROM candidate.notices c WHERE c.dedupe_key=m.dedupe_key)
                """).fetchone()[0]
                report["candidate_keys_new"] = baseline.execute("""
                    SELECT COUNT(*) FROM candidate.notices c WHERE NOT EXISTS
                    (SELECT 1 FROM main.notices m WHERE m.dedupe_key=c.dedupe_key)
                """).fetchone()[0]
                report["state_deltas"] = {
                    state: {
                        "notices": report["states"].get(state, {}).get("notices", 0)
                        - before["notices"],
                        "workers": report["states"].get(state, {}).get("workers", 0)
                        - before["workers"],
                    }
                    for state, before in report["baseline_states"].items()
                }
            finally:
                baseline.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--observed-at", required=True, help="YYYY-MM-DD for synthetic rebuild observations")
    parser.add_argument("--compare-db", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    logger.setLevel(logging.ERROR)
    result = rebuild(args.bundle, args.db, args.observed_at, args.compare_db)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "notices", "versions", "links", "workers", "integrity", "foreign_key_errors"
    )}, sort_keys=True))
