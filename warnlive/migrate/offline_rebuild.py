"""Rebuild an isolated WARN candidate from a frozen local source bundle.

No network requests are made. The default, legacy comparison mode uses a
bundled old-DB overlap policy. Source-only mode ignores that policy, gives
agency archives priority over BLN, and quarantines uncertain overlaps. Neither
mode promotes or exports its candidate.
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
    added = updated = unchanged = coalesced = collisions = 0
    for state, records in sorted(groups.items()):
        if not records:
            continue
        stats = ingest(conn, records, observed_at=observed_at)
        added += stats.new
        updated += stats.updated
        unchanged += stats.unchanged
        coalesced += stats.coalesced
        collisions += stats.suspected_collisions
    return {"new": added, "updated": updated, "unchanged": unchanged,
            "coalesced": coalesced,
            "suspected_collisions": collisions}


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


def _backfill_raw(
    conn, raw_dir: Path, observed_at: str, exceptions: list[dict] | None = None,
) -> dict:
    registry = load_registry()
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    report = {"files": 0, "raw_rows": 0, "parse_failures": 0,
              "skipped_existing": 0, "quarantined_conflicts": 0,
              "quarantined_missing_source_identity": 0,
              "coalesced_identical_rows": 0}
    groups_by_state: dict[str, list[dict]] = defaultdict(list)
    for source in sorted(raw_dir.glob("*.csv")):
        postal = source.stem
        if postal in {"ga", "sc"} or postal not in registry:
            continue
        norm = normalize_file(postal, raw_dir, registry[postal].source_url)
        report["files"] += 1
        report["raw_rows"] += norm.raw_rows
        report["parse_failures"] += norm.failed_rows
        if exceptions is not None:
            exceptions.extend(
                _exception(f"backfill/raw/{postal}.csv", "parse_failure", failure)
                for failure in norm.failures
            )
        by_key: dict[str, list[dict]] = defaultdict(list)
        for rec in norm.records:
            by_key[rec["dedupe_key"]].append(rec)
        for key, rows in by_key.items():
            if postal == "ks" and any(not row.get("source_identity") for row in rows):
                report["quarantined_missing_source_identity"] += len(rows)
                if exceptions is not None:
                    exceptions.extend(
                        _exception(f"backfill/raw/{postal}.csv", "missing_source_identity", row)
                        for row in rows
                    )
            elif key in seen:
                report["skipped_existing"] += len(rows)
            elif len({row["raw_record_hash"] for row in rows}) > 1:
                report["quarantined_conflicts"] += len(rows)
                if exceptions is not None:
                    exceptions.extend(
                        _exception(f"backfill/raw/{postal}.csv", "conflicting_same_key", row)
                        for row in rows
                    )
            else:
                groups_by_state[postal.upper()].append(rows[0])
                report["coalesced_identical_rows"] += len(rows) - 1
                seen.add(key)
    stats = _ingest_groups(conn, groups_by_state, observed_at)
    accounted = sum(report[name] for name in (
        "parse_failures", "skipped_existing", "quarantined_conflicts",
        "quarantined_missing_source_identity", "coalesced_identical_rows",
    )) + sum(stats[name] for name in ("new", "updated", "unchanged", "coalesced"))
    return {**report, **stats, "unaccounted_rows": report["raw_rows"] - accounted}


def _cached_only(_url: str, dest: Path) -> bytes | None:
    return dest.read_bytes() if dest.is_file() else None


def _exception(origin: str, reason: str, rec: dict) -> dict:
    """Stable pointer back to a preserved row, without a guessed decision."""
    item = {
        "origin": origin, "reason": reason, "state": rec.get("state"),
        "dedupe_key": rec.get("dedupe_key"),
        "raw_record_hash": rec.get("raw_record_hash"),
        "source_row_sha256": rec.get("source_row_sha256"),
        "source_identity": rec.get("source_identity"),
        "source_notice_id": rec.get("source_notice_id"),
        "source_url": rec.get("source_url"),
        "raw_extra": rec.get("raw_extra"),
        "prepared_row": rec.get("prepared_row"),
        "error": rec.get("error"),
    }
    if origin == "agency-cache:CA":
        try:
            year = json.loads(rec.get("raw_extra") or "{}").get("year_file")
        except (TypeError, ValueError):
            year = None
        if isinstance(year, int) and 2000 <= year <= 2014:
            item["bundle_artifact"] = f"backfill/cache/archives/ca/{year}.pdf"
    return item


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
    conn, cache: Path, accepted: set[str] | None, source_urls: dict[str, str],
    observed_at: str, exceptions: list[dict] | None = None,
) -> dict:
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    report = {}
    for state in ("WI", "FL", "CA", "MA", "OH", "NY"):
        # This is a snapshot of the higher-priority input's occupied months,
        # not a set updated for each archive row: one archive may legitimately
        # contain many independent filings in the same month.
        occupied = {
            value[:7]
            for row in conn.execute(
                "SELECT notice_date, effective_date FROM notices WHERE state = ?",
                (state,),
            )
            for value in row if value
        } if accepted is None else set()
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
        skipped = ambiguous = outside_policy = overlap = undated = coalesced = 0
        for key, rows in by_key.items():
            if key in seen:
                skipped += len(rows)
            elif len({row["raw_record_hash"] for row in rows}) > 1:
                ambiguous += len(rows)
                if exceptions is not None:
                    exceptions.extend(
                        _exception(f"agency-cache:{state}", "conflicting_same_key", row)
                        for row in rows
                    )
            elif accepted is not None and key not in accepted:
                outside_policy += len(rows)
            else:
                rec = rows[0]
                if accepted is None:
                    dates = [rec.get(name) for name in ("notice_date", "effective_date")]
                    months = {value[:7] for value in dates if value}
                    if not months:
                        undated += len(rows)
                        if exceptions is not None:
                            exceptions.extend(
                                _exception(f"agency-cache:{state}", "no_date", row)
                                for row in rows
                            )
                        continue
                    if months & occupied:
                        overlap += len(rows)
                        if exceptions is not None:
                            exceptions.extend(
                                _exception(f"agency-cache:{state}", "occupied_source_month", row)
                                for row in rows
                            )
                        continue
                    if state == "CA" and rec.get("source_url") == "cached://annual-pdf":
                        artifact = _exception("agency-cache:CA", "included", rec).get(
                            "bundle_artifact"
                        )
                        if artifact:
                            rec["source_details"] = json.dumps(
                                {"source_artifact": artifact}, sort_keys=True
                            )
                            rec["source_url"] = None
                            rec["raw_record_hash"] = _record_hash(rec)
                elif key in source_urls:
                    rec["source_url"] = source_urls[key]
                    rec["raw_record_hash"] = _record_hash(rec)
                eligible.append(rec)
                coalesced += len(rows) - 1
                seen.add(key)
        stats = _ingest_groups(conn, {state: eligible}, observed_at)
        accounted = (
            skipped + ambiguous + outside_policy + overlap + undated + coalesced
            + stats["new"] + stats["updated"] + stats["unchanged"]
            + stats["coalesced"]
        )
        report[state] = {"parsed": len(records), "already": skipped,
                         "outside_policy": outside_policy,
                         "ambiguous_rows": ambiguous,
                         "coalesced_identical_rows": coalesced,
                         "source_overlap_rows": overlap,
                         "undated_rows": undated,
                         "unaccounted_rows": len(records) - accounted, **stats}
    return report


def _bln_unresolved(
    conn, source: Path, exceptions: list[dict] | None = None,
) -> dict:
    """Count BLN rows not selected by conservative source-only rules."""
    from warnlive.normalize.engine import _fold

    registry = load_registry()
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    signatures: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT state, COALESCE(notice_date, effective_date), employer_name, "
        "employees_affected, dedupe_key, location FROM notices"
    ):
        state, date, employer, workers, key, location = row
        signatures[(state, date, _fold(employer), workers)].append(
            (key, _fold(location))
        )
    by_state: dict[str, int] = defaultdict(int)
    triage: dict[str, int] = defaultdict(int)
    total = represented = invalid = superseded = identity_excluded = 0

    def raw_exception(row: dict, reason: str, error: str | None = None) -> dict:
        raw_extra = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return {
            "origin": "backfill/bln_integrated.csv", "reason": reason,
            "state": (row.get("postal_code") or "").upper(),
            "dedupe_key": None,
            "source_row_sha256": hashlib.sha256(raw_extra.encode()).hexdigest(),
            "source_notice_id": row.get("hash_id"),
            "raw_extra": raw_extra, "error": error,
        }

    with source.open(newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            state = (row.get("postal_code") or "").upper()
            if state.lower() not in registry:
                invalid += 1
                if exceptions is not None:
                    exceptions.append(raw_exception(row, "unsupported_state"))
                continue
            if row.get("is_superseded") == "True":
                superseded += 1
                if exceptions is not None:
                    exceptions.append(raw_exception(row, "superseded_source_row"))
                continue
            if state in {"GA", "SC", "IA"}:
                identity_excluded += 1
                if exceptions is not None:
                    exceptions.append(raw_exception(row, "source_identity_unresolved"))
                continue
            try:
                rec = bln_integrated.to_canonical(row, registry[state.lower()].source_url)
            except (ValueError, KeyError, TypeError) as exc:
                invalid += 1
                if exceptions is not None:
                    exceptions.append(raw_exception(
                        row, "parse_failure", f"{type(exc).__name__}: {exc}"
                    ))
                continue
            if not rec["employer_name"]:
                invalid += 1
                if exceptions is not None:
                    exceptions.append(raw_exception(row, "missing_employer"))
            elif rec["dedupe_key"] not in seen:
                by_state[state] += 1
                signature = (
                    state, rec["notice_date"] or rec["effective_date"],
                    _fold(rec["employer_name"]), rec["employees_affected"],
                )
                matches = signatures.get(signature, [])
                if not matches:
                    category = "no_exact_signature"
                elif len(matches) > 1:
                    category = "multiple_exact_signatures"
                elif matches[0][1] == _fold(rec["location"]):
                    category = "one_exact_signature_same_location"
                else:
                    category = "one_exact_signature_different_location"
                triage[category] += 1
                if exceptions is not None:
                    item = _exception(
                        "backfill/bln_integrated.csv", "unmatched_after_conservative_fill", rec
                    )
                    item["triage"] = category
                    item["candidate_keys"] = sorted(key for key, _ in matches)
                    exceptions.append(item)
            else:
                represented += 1
    accounted = represented + invalid + superseded + identity_excluded + sum(by_state.values())
    return {"total_rows": total, "represented_by_key": represented,
            "unresolved_rows": sum(by_state.values()),
            "by_state": dict(sorted(by_state.items())),
            "triage": dict(sorted(triage.items())),
            "invalid_rows": invalid, "superseded_rows": superseded,
            "identity_excluded_rows": identity_excluded,
            "unaccounted_rows": total - accounted}


def _write_exceptions(path: Path, rows: list[dict]) -> dict:
    """Write a reproducible, non-overwriting queue for source review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    ordered = sorted(rows, key=lambda r: (
        r.get("origin") or "", r.get("state") or "",
        r.get("dedupe_key") or "",
        r.get("raw_record_hash") or r.get("source_row_sha256") or "",
        r.get("reason") or "", r.get("prepared_row") or 0,
    ))
    created = False
    try:
        with path.open("xb") as output:
            created = True
            for row in ordered:
                line = (json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n").encode()
                output.write(line)
                digest.update(line)
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
        raise
    return {"path": str(path), "rows": len(ordered), "sha256": digest.hexdigest()}


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


def rebuild(
    bundle: Path, out_db: Path, observed_at: str,
    compare_db: Path | None = None, *, source_only: bool = False,
    exceptions_path: Path | None = None,
) -> dict:
    if out_db.exists():
        raise FileExistsError(f"candidate database already exists: {out_db}")
    if source_only:
        exceptions_path = exceptions_path or out_db.with_suffix(".exceptions.jsonl")
        if exceptions_path.resolve() == out_db.resolve():
            raise ValueError("exception manifest path must differ from candidate database")
        if exceptions_path.exists():
            raise FileExistsError(f"exception manifest already exists: {exceptions_path}")
    with TemporaryDirectory(prefix="warn-rebuild-sources-") as temp:
        source = Path(temp) / "sources"
        manifest = extract(bundle, source)
        policy = None if source_only else _read_policy(source / "rebuild_policy.json")
        accepted = None if source_only else set(policy["accepted_keys"])
        exceptions: list[dict] = []
        la_source_rows: list[dict] = []
        if (source / "agency/la").is_dir():
            from warnlive.migrate.la_source import extract as extract_la

            la_source_rows = extract_la(source / "agency/la")
            if source_only:
                for row in la_source_rows:
                    reason = (
                        "official_source_annotation" if row["kind"] == "annotation"
                        else "official_source_rescinded" if row["status"] == "rescinded"
                        else "official_source_unreviewed"
                    )
                    exceptions.append({
                        "origin": row["source_artifact"], "state": "LA",
                        "reason": reason, "source_row": row["source_row"],
                        "source_row_sha256": row["source_row_sha256"],
                        "source_url": row["source_url"],
                        "raw_extra": json.dumps(row, sort_keys=True, ensure_ascii=False),
                    })
        raw_report = build_raw(
            out_db, source / "raw", source / "cache/sc",
            observed_at=observed_at,
            exceptions=exceptions if source_only else None,
        )
        conn = db_mod.connect(out_db)
        try:
            archive_cache = source / "backfill/cache"
            if source_only:
                # Official agency artifacts and saved state raw snapshots
                # outrank the transformed BLN copy.
                agencies = _cached_agencies(
                    conn, archive_cache, None, {}, observed_at, exceptions
                )
                backfill_raw = _backfill_raw(
                    conn, source / "backfill/raw", observed_at, exceptions
                )
                backfill = _bln_conservative(
                    conn, source / "backfill/bln_integrated.csv", observed_at
                )
                accepted_bln = _bln_unresolved(
                    conn, source / "backfill/bln_integrated.csv", exceptions
                )
            else:
                backfill = _bln_conservative(
                    conn, source / "backfill/bln_integrated.csv", observed_at
                )
                backfill_raw = _backfill_raw(conn, source / "backfill/raw", observed_at)
                agencies = _cached_agencies(
                    conn, archive_cache, accepted,
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
                "policy": "source-only-v1" if source_only else policy["format"],
                "raw": raw_report["states"],
                "la_official_source": {
                    "table_rows": len(la_source_rows),
                    "notice_rows": sum(row["kind"] == "notice" for row in la_source_rows),
                    "rescinded_notice_rows": sum(
                        row.get("status") == "rescinded" for row in la_source_rows
                    ),
                    "annotation_rows": sum(
                        row["kind"] == "annotation" for row in la_source_rows
                    ),
                    "ingested_rows": 0,
                },
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
            if source_only:
                report["exceptions"] = _write_exceptions(exceptions_path, exceptions)
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
    parser.add_argument("--source-only", action="store_true",
                        help="Ignore old-DB accepted keys; quarantine uncertain source overlaps")
    parser.add_argument("--exceptions", type=Path,
                        help="Source-only JSONL exception manifest (default: beside candidate DB)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    logger.setLevel(logging.ERROR)
    result = rebuild(args.bundle, args.db, args.observed_at, args.compare_db,
                     source_only=args.source_only, exceptions_path=args.exceptions)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in (
        "notices", "versions", "links", "workers", "integrity", "foreign_key_errors"
    )}, sort_keys=True))
