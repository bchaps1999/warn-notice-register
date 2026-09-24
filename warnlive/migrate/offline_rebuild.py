"""Rebuild an isolated WARN candidate from frozen agency-source artifacts.

No network requests are made. The candidate does not use the previous database
or Big Local News historical datasets for admission. It is not promoted here.
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
from warnlive.normalize.engine import _fold, _record_hash, normalize_file
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


def _bln_conservative(
    conn, source: Path, observed_at: str, excluded: dict[str, dict] | None = None,
    selected_source_ids: set[str] | None = None,
    coalesced_source_ids: dict[str, str] | None = None,
) -> dict:
    registry = load_registry()
    # These states' BLN rows cannot be keyed to a trustworthy source filing
    # identity. IL, KY, MA, NV, and WA raw tables have agency reporting/receipt
    # dates rather than verified legal notice dates, so older BLN rows must not be admitted as a side
    # effect of clearing those canonical fields. The ledger quarantines them;
    # the importer must enforce the same boundary.
    allowed = [cfg.postal for cfg in registry.all()
               if cfg.postal not in {"ga", "sc", "ia", "il", "nj", "ky", "ma", "nv", "wa"}]
    report = {}
    for label, select in (
        ("older", bln_integrated.older_rows_by_state),
        ("empty_months", bln_integrated.gap_rows_by_state),
    ):
        groups = select(source, conn, registry, states=allowed)
        selected_rows = sum(map(len, groups.values()))
        excluded_rows = 0
        if excluded:
            for state, records in groups.items():
                kept = [rec for rec in records if rec.get("source_notice_id") not in excluded]
                excluded_rows += len(records) - len(kept)
                groups[state] = kept
        if selected_source_ids is not None:
            selected_source_ids.update(
                rec["source_notice_id"] for records in groups.values() for rec in records
                if rec.get("source_notice_id")
            )
        if coalesced_source_ids is not None:
            for records in groups.values():
                previous_by_key: dict[str, dict] = {}
                for rec in records:
                    previous = previous_by_key.get(rec["dedupe_key"])
                    if previous and previous["raw_record_hash"] == rec["raw_record_hash"]:
                        coalesced_source_ids[rec["source_notice_id"]] = previous["source_notice_id"]
                    else:
                        previous_by_key[rec["dedupe_key"]] = rec
        report[label] = {"input_rows": selected_rows,
                         "official_overlay_excluded_rows": excluded_rows,
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
              "quarantined_historical_nj": 0,
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
            if postal in {"il", "ks", "nj"} and any(not row.get("source_identity") for row in rows):
                report["quarantined_missing_source_identity"] += len(rows)
                if exceptions is not None:
                    exceptions.extend(
                        _exception(f"backfill/raw/{postal}.csv", "missing_source_identity", row)
                        for row in rows
                    )
            elif key in seen:
                report["skipped_existing"] += len(rows)
                if exceptions is not None:
                    survivor = conn.execute(
                        "SELECT id, source_identity FROM notices WHERE dedupe_key = ?",
                        (key,),
                    ).fetchone()
                    if survivor is None:
                        raise ValueError(f"backfill key has no admitted survivor: {key}")
                    for row in rows:
                        item = _exception(
                            f"backfill/raw/{postal}.csv",
                            "matched_existing_key_not_admitted", row,
                        )
                        item.update({
                            "match_basis": "canonical_key_only",
                            "survivor_notice_id": survivor["id"],
                            "survivor_source_identity": survivor["source_identity"],
                        })
                        exceptions.append(item)
            elif postal == "nj":
                # The older NJ capture has no filing identifier. An unmatched
                # raw row may be a new event, changed transcription, or
                # amendment; exclude it from notice totals.
                report["quarantined_historical_nj"] += len(rows)
                if exceptions is not None:
                    exceptions.extend(
                        _exception("backfill/raw/nj.csv", "historical_event_identity_unresolved", row)
                        for row in rows
                    )
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
                if exceptions is not None:
                    for row in rows[1:]:
                        item = _exception(
                            f"backfill/raw/{postal}.csv", "coalesced_same_key_content", row,
                        )
                        item["match_basis"] = "canonical_key_and_content"
                        item["survivor_prepared_row"] = rows[0].get("prepared_row")
                        exceptions.append(item)
                seen.add(key)
    stats = _ingest_groups(conn, groups_by_state, observed_at)
    accounted = sum(report[name] for name in (
        "parse_failures", "skipped_existing", "quarantined_conflicts",
        "quarantined_missing_source_identity", "quarantined_historical_nj",
        "coalesced_identical_rows",
    )) + sum(stats[name] for name in ("new", "updated", "unchanged", "coalesced"))
    return {**report, **stats, "unaccounted_rows": report["raw_rows"] - accounted}


def _cached_only(_url: str, dest: Path) -> bytes | None:
    return dest.read_bytes() if dest.is_file() else None


def _notice_year(notice_date: object) -> str | None:
    return (
        notice_date[:4]
        if isinstance(notice_date, str) and len(notice_date) >= 5
        and notice_date[:4].isdigit() and notice_date[4] == "-"
        else None
    )


def _exception(origin: str, reason: str, rec: dict) -> dict:
    """Stable pointer back to a preserved row, without a guessed decision."""
    notice_year = _notice_year(rec.get("notice_date"))
    raw_extra = rec.get("raw_extra")
    source_row_sha256 = rec.get("source_row_sha256")
    if source_row_sha256 is None and isinstance(raw_extra, str):
        source_row_sha256 = hashlib.sha256(raw_extra.encode()).hexdigest()
    item = {
        "origin": origin, "reason": reason, "state": rec.get("state"),
        "notice_year": notice_year,
        "dedupe_key": rec.get("dedupe_key"),
        "raw_record_hash": rec.get("raw_record_hash"),
        "source_row_sha256": source_row_sha256,
        "source_identity": rec.get("source_identity"),
        "source_notice_id": rec.get("source_notice_id"),
        "source_url": rec.get("source_url"),
        "raw_extra": raw_extra,
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
        # A statewide occupied month says nothing about whether two employers'
        # rows describe the same notice. Screen only a plausible employer/event
        # overlap; leave source-specific identity review to the projector.
        occupied: set[tuple[str, str]] = set()
        if accepted is None:
            for employer, notice, effective, detail_text in conn.execute(
                "SELECT employer_name, notice_date, effective_date, source_details "
                "FROM notices WHERE state = ?", (state,),
            ):
                dates = [notice, effective]
                if state == "MA":
                    # Preserve the old month-overlap boundary while moving
                    # agency receipt out of legal notice_date.
                    detail = json.loads(detail_text or "{}")
                    dates.append(detail.get("agency_received_date") or
                                 detail.get("legacy_notice_key_date"))
                occupied.update((_fold(employer), value[:7]) for value in dates if value)
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
                if exceptions is not None:
                    for row in rows:
                        item = _exception(
                            f"agency-cache:{state}", "matched_existing_key_not_admitted", row,
                        )
                        item.update({"match_basis": "canonical_key_only",
                                     "survivor_dedupe_key": key})
                        exceptions.append(item)
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
                    # Florida's archive often reports a layoff interval months
                    # after the filing. Use its notice month for overlap
                    # screening so parsing that interval cannot make a
                    # distinct notice disappear from the source-only replay.
                    if state == "FL" and rec.get("notice_date"):
                        dates = [rec["notice_date"]]
                    else:
                        dates = [rec.get(name) for name in ("notice_date", "effective_date")]
                        if state == "MA":
                            detail = json.loads(rec.get("source_details") or "{}")
                            dates.append(detail.get("agency_received_date") or
                                         detail.get("legacy_notice_key_date"))
                    months = {value[:7] for value in dates if value}
                    if not months:
                        undated += len(rows)
                        if exceptions is not None:
                            exceptions.extend(
                                _exception(f"agency-cache:{state}", "no_date", row)
                                for row in rows
                            )
                        continue
                    employer = _fold(rec.get("employer_name"))
                    if any((employer, month) in occupied for month in months):
                        overlap += len(rows)
                        if exceptions is not None:
                            exceptions.extend(
                                _exception(f"agency-cache:{state}", "possible_employer_month_overlap", row)
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
                if exceptions is not None:
                    for row in rows[1:]:
                        item = _exception(
                            f"agency-cache:{state}", "coalesced_same_key_content", row,
                        )
                        item["match_basis"] = "canonical_key_and_content"
                        item["survivor_source_notice_id"] = rec.get("source_notice_id")
                        exceptions.append(item)
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
    excluded: dict[str, dict] | None = None,
    admitted_source_ids: set[str] | None = None,
    coalesced_source_ids: dict[str, str] | None = None,
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
    total = represented = invalid = superseded = identity_excluded = overlay_excluded = 0

    def raw_exception(row: dict, reason: str, error: str | None = None) -> dict:
        raw_extra = json.dumps(row, sort_keys=True, ensure_ascii=False)
        return {
            "origin": "backfill/bln_integrated.csv", "reason": reason,
            "source_row": ordinal,
            "state": (row.get("postal_code") or "").upper(),
            "notice_year": _notice_year(row.get("notice_date")),
            "dedupe_key": None,
            "source_row_sha256": hashlib.sha256(raw_extra.encode()).hexdigest(),
            "source_notice_id": row.get("hash_id"),
            "raw_extra": raw_extra, "error": error,
        }

    with source.open(newline="") as fh:
        for ordinal, row in enumerate(csv.DictReader(fh), start=1):
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
                    item = raw_exception(row, "superseded_source_row")
                    mapping = (excluded or {}).get(row.get("hash_id"))
                    if mapping is not None:
                        item.update({"source_row": ordinal,
                                     "official_source_rows": mapping["official_source_rows"],
                                     "match_basis": mapping["match_basis"]})
                    exceptions.append(item)
                continue
            mapping = (excluded or {}).get(row.get("hash_id"))
            if mapping is not None:
                raw = json.dumps(row, sort_keys=True, ensure_ascii=False)
                if (mapping["source_row"] != ordinal or
                        mapping["source_row_sha256"] != hashlib.sha256(raw.encode()).hexdigest()):
                    raise ValueError("reviewed BLN source row drift")
                overlay_excluded += 1
                if exceptions is not None:
                    reason = {
                        "accepted": "superseded_by_official_source",
                        "held": "held_with_official_source",
                        "rescinded": "rescinded_by_official_source",
                        "reviewed_duplicate": "duplicate_observation_with_reviewed_survivor",
                    }[mapping["disposition"]]
                    item = raw_exception(row, reason)
                    item.update({"source_row": ordinal,
                                 "official_source_rows": mapping["official_source_rows"],
                                 "match_basis": mapping["match_basis"]})
                    if mapping["disposition"] == "reviewed_duplicate":
                        item.update({
                            "survivor_source_notice_id": mapping["survivor_source_notice_id"],
                            "survivor_expected_dedupe_key": mapping["survivor_expected_dedupe_key"],
                            "reviewed_decision_id": mapping["reviewed_decision_id"],
                        })
                    exceptions.append(item)
                continue
            if state in {"GA", "SC", "IA", "IL", "NJ", "KY", "MA", "NV", "WA"}:
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
                    item["source_row"] = ordinal
                    item["triage"] = category
                    item["candidate_keys"] = sorted(key for key, _ in matches)
                    exceptions.append(item)
            else:
                represented += 1
                if exceptions is not None and coalesced_source_ids and row.get("hash_id") in coalesced_source_ids:
                    item = raw_exception(row, "coalesced_same_key_content")
                    item.update({
                        "match_basis": "canonical_key_and_content",
                        "survivor_source_notice_id": coalesced_source_ids[row["hash_id"]],
                    })
                    exceptions.append(item)
                elif exceptions is not None and (
                    admitted_source_ids is None or row.get("hash_id") not in admitted_source_ids
                ):
                    item = raw_exception(row, "matched_existing_key_not_admitted")
                    item.update({
                        "match_basis": "canonical_key_only",
                        "survivor_dedupe_key": rec["dedupe_key"],
                    })
                    exceptions.append(item)
    accounted = (represented + invalid + superseded + identity_excluded +
                 overlay_excluded + sum(by_state.values()))
    return {"total_rows": total, "represented_by_key": represented,
            "unresolved_rows": sum(by_state.values()),
            "by_state": dict(sorted(by_state.items())),
            "triage": dict(sorted(triage.items())),
            "invalid_rows": invalid, "superseded_rows": superseded,
            "identity_excluded_rows": identity_excluded,
            "official_overlay_excluded_rows": overlay_excluded,
            "unaccounted_rows": total - accounted}


def _write_exceptions(path: Path, rows: list[dict]) -> dict:
    """Write a reproducible, non-overwriting excluded-observation manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    ordered = sorted(rows, key=lambda r: (
        r.get("origin") or "", r.get("state") or "",
        r.get("dedupe_key") or "",
        r.get("raw_record_hash") or r.get("source_row_sha256") or "",
        r.get("reason") or "", r.get("prepared_row") or 0,
    ))
    coverage: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for row in ordered:
        key = (row.get("state") or "unknown", row.get("origin") or "unknown",
               row.get("notice_year") or "unknown", row.get("reason") or "unknown")
        coverage[key] += 1
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
    return {
        "path": str(path), "rows": len(ordered), "sha256": digest.hexdigest(),
        "by_state_source_year_reason": [
            {"state": state, "source": source, "notice_year": year,
             "reason": reason, "rows": count}
            for (state, source, year, reason), count in sorted(coverage.items())
        ],
    }


def _verify_source_row_accounting(report: dict, exceptions: list[dict]) -> dict:
    """Require each primary source layer's excluded rows to appear in the ledger."""
    by_origin: dict[str, int] = defaultdict(int)
    current_raw_rows = 0
    for item in exceptions:
        origin = item.get("origin") or ""
        if origin.startswith("raw/") or origin == "cache/sc":
            # Separate evidence checks and missing-file markers reuse a raw
            # origin but are not transformed source-row dispositions.
            if item.get("prepared_row") is not None:
                current_raw_rows += 1
        elif origin.startswith("agency-cache:"):
            by_origin["agency_cache"] += 1
    by_origin["current_raw"] = current_raw_rows

    raw = report["raw"]
    agency = report["cached_agencies"]
    expected = {
        "current_raw": sum(
            item.get("raw_rows", 0) - sum(item.get(key, 0) for key in (
                "new", "updated", "unchanged"))
            for item in raw.values()
        ),
        "agency_cache": sum(
            item["parsed"] - sum(item.get(key, 0) for key in (
                "new", "updated", "unchanged"))
            for item in agency.values()
        ),
    }
    unaccounted = (
        sum(item.get("unaccounted_rows", 0) for item in raw.values())
        + sum(item.get("unaccounted_rows", 0) for item in agency.values())
    )
    if unaccounted or any(by_origin[layer] != count for layer, count in expected.items()):
        raise ValueError(
            "source-row accounting mismatch: "
            f"expected={expected}, ledger={dict(by_origin)}, unaccounted={unaccounted}"
        )
    return {layer: {"excluded_rows": expected[layer]} for layer in expected}


def _admitted_coverage(conn: sqlite3.Connection) -> list[dict]:
    """Count published notices without implying one notice per source row."""
    return [dict(row) for row in conn.execute("""
        SELECT state, COALESCE(source_url, 'unknown') AS source,
               CASE WHEN notice_date GLOB '[0-9][0-9][0-9][0-9]-*'
                    THEN substr(notice_date, 1, 4) ELSE 'unknown' END AS notice_year,
               COUNT(*) AS notices,
               COALESCE(SUM(employees_affected), 0) AS workers
        FROM notices GROUP BY state, source, notice_year
        ORDER BY state, source, notice_year
    """)]


def _bln_accepted(conn, source: Path, accepted: set[str], observed_at: str) -> dict:
    registry = load_registry()
    seen = {row[0] for row in conn.execute("SELECT dedupe_key FROM notices")}
    by_key: dict[str, list[dict]] = defaultdict(list)
    eligible_rows = 0
    with source.open(newline="") as fh:
        for row in csv.DictReader(fh):
            state = (row.get("postal_code") or "").upper()
            if (state in {"GA", "SC", "IA", "NJ"} or state.lower() not in registry
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
        "notice_date_precision,notice_date_basis,effective_date_precision,"
        "effective_date_basis,effective_date_end_precision,effective_date_end_basis,"
        "source_identity,source_details "
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


def _repair_il_from_cache(
    db: Path, cache: Path, observed_at: str | None = None,
) -> dict:
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
        il_effective_dates.callback("", cache.parent.parent, db, False, observed_at)
    return {"cached_files": len(files), "parsed_records": len(records),
            "failed_files": failed, "result": output.getvalue().strip()}


def rebuild(
    bundle: Path, out_db: Path, observed_at: str,
    compare_db: Path | None = None, *, source_only: bool = True,
    exceptions_path: Path | None = None,
) -> dict:
    if not source_only:
        raise ValueError("legacy overlap-policy rebuild retired; use --source-only")
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
        if manifest.get("admission_inputs") != "agency-only-v1":
            raise ValueError("agency-only rebuild requires a newly derived agency-only bundle")
        forbidden = [item["path"] for item in manifest["files"] if
                     item["path"] == "backfill/bln_integrated.csv"
                     or item["path"].startswith("backfill/raw/")
                     or item["path"] in {"cache/ga/ga_historical.csv", "cache/tn/tn_historical.csv"}
                     or item["path"] == "rebuild_policy.json"]
        if forbidden:
            raise ValueError(f"agency-only rebuild forbids BLN or old-DB inputs: {forbidden[:3]}")
        from warnlive.migrate.source_bundle import validate_agency_raw

        validate_agency_raw(source / "raw")
        il_dir = source / "agency/il"
        if il_dir.is_dir():
            from warnlive.migrate.il_bundle import _check_artifacts

            il_artifacts = _check_artifacts(il_dir)
            if (source / "raw/il.csv").read_bytes() != il_artifacts["il.csv"]:
                raise ValueError("Illinois agency CSV differs from current raw/il.csv")
        source_bundle_sha256 = hashlib.sha256(Path(bundle).read_bytes()).hexdigest() if source_only else None
        exceptions: list[dict] = []
        la_source_rows: list[dict] = []
        ia_current_rows: list[dict] = []
        ia_historical_rows: list[dict] = []
        ia_records: list[dict] = []
        ia_source_report = None
        ia_related: dict[str, str] = {}
        ky_source_rows: list[dict] = []
        ky_records: list[dict] = []
        ky_source_report = None
        if (source / "agency/ky").is_dir():
            from warnlive.migrate.ky_source import read_artifacts as read_ky, project as project_ky

            ky_source_rows = read_ky(source / "agency/ky")
            ky_records, ky_held, ky_source_report = project_ky(source / "agency/ky")
            if len(ky_records) + len(ky_held) != len(ky_source_rows):
                raise ValueError("Kentucky agency rows are not fully accounted for")
            exceptions.extend(ky_held)
        or_records: list[dict] = []
        or_source_report = None
        or_historical_dir = source / "agency/or_historical"
        tx_historical_dir = source / "agency/tx_historical"
        mo_annual_dir = source / "agency/mo_annual"
        mo_historical_dir = source / "agency/mo_historical"
        oh_annual_dir = source / "agency/oh_annual"
        if (source / "agency/or").is_dir():
            from warnlive.migrate.or_source import project as project_or

            or_records, or_held, or_source_report = project_or(source / "agency/or")
            exceptions.extend(or_held)
        tn_records: list[dict] = []
        tn_source_report = None
        if (source / "agency/tn").is_dir():
            from warnlive.migrate.tn_source import project as project_tn

            tn_records, tn_held, tn_source_report = project_tn(
                source / "agency/tn", source / "raw/tn.csv")
            exceptions.extend(tn_held)
        ny_records: list[dict] = []
        ny_source_report = None
        ny_annual_dir = source / "agency/ny_annual"
        ga_archive_dir = source / "agency/ga"
        if not ny_annual_dir.is_dir() and (source / "agency/ny").is_dir():
            from warnlive.migrate.ny_overlay import project as project_ny

            ny_records, ny_held, ny_source_report = project_ny(source / "agency/ny")
            exceptions.extend(ny_held)
        if (source / "agency/ia").is_dir():
            from warnlive.migrate import ia_source

            ia_current_rows = ia_source.extract(source / "agency/ia")
            ia_historical_rows = ia_source.extract_historical(source / "agency/ia")
            if source_only:
                ia_records, ia_held, ia_source_report, ia_related = ia_source.project(
                    ia_current_rows + ia_historical_rows)
                exceptions.extend(ia_held)
        if (source / "agency/la").is_dir():
            from warnlive.migrate.la_source import extract as extract_la
            from warnlive.migrate import la_overlay

            la_source_rows = extract_la(source / "agency/la")
            if source_only:
                for path in (source / "raw/la.csv", source / "backfill/raw/la.csv"):
                    if path.exists():
                        raise ValueError(f"Louisiana overlay has unreviewed alternate source: {path}")
                la_overlay.validate_source_rows(la_source_rows, source / "agency/la")
                exceptions.extend(la_overlay.source_exceptions(la_source_rows))
        raw_report = build_raw(
            out_db, source / "raw", source / "cache/sc",
            observed_at=observed_at,
            exceptions=exceptions if source_only else None,
        )
        conn = db_mod.connect(out_db)
        try:
            archive_cache = source / "backfill/cache"
            if source_only:
                # Only frozen state captures and agency archives enter this build.
                agencies = _cached_agencies(
                    conn, archive_cache, None, {}, observed_at, exceptions
                )
                la_ingest = _ingest_groups(conn, {"LA": [
                    la_overlay.record(row) for row in la_source_rows
                    if row["kind"] == "notice" and row["source_row"] in la_overlay.EMPLOYERS
                ]}, observed_at) if la_source_rows else {"new": 0}
                ia_ingest = _ingest_groups(conn, {"IA": ia_records}, observed_at)
                if ia_source_report is not None and ia_ingest["new"] != len(ia_records):
                    raise ValueError("Iowa source rows did not produce unique notices")
                ky_ingest = _ingest_groups(conn, {"KY": ky_records}, observed_at)
                if ky_source_report is not None and ky_ingest["new"] != len(ky_records):
                    raise ValueError("Kentucky agency rows did not produce unique notices")
                or_ingest = _ingest_groups(conn, {"OR": or_records}, observed_at)
                if or_source_report is not None and or_ingest["new"] != len(or_records):
                    raise ValueError("Oregon agency rows did not produce unique notices")
                or_historical_report = None
                or_historical_ingest = {"new": 0}
                if or_historical_dir.is_dir():
                    if not (source / "agency/or").is_dir():
                        raise ValueError("Oregon historical source requires newer agency captures")
                    from warnlive.migrate.or_source import read_artifacts as read_or_current
                    from warnlive.migrate.or_historical_source import project as project_or_historical

                    current_or_rows, _ = read_or_current(source / "agency/or")
                    current_or_ids = {str(row["raw"]["WARN#"] or "") for row in current_or_rows}
                    or_historical_records, or_historical_held, or_historical_report = (
                        project_or_historical(or_historical_dir, current_or_ids)
                    )
                    exceptions.extend(or_historical_held)
                    or_historical_ingest = _ingest_groups(
                        conn, {"OR": or_historical_records}, observed_at)
                    if or_historical_ingest["new"] != len(or_historical_records):
                        raise ValueError("Oregon historical rows did not produce unique notices")
                tn_ingest = _ingest_groups(conn, {"TN": tn_records}, observed_at)
                if tn_source_report is not None and tn_ingest["new"] != len(tn_records):
                    raise ValueError("Tennessee agency rows did not produce unique notices")
                tx_historical_report = None
                tx_historical_ingest = {"new": 0}
                if tx_historical_dir.is_dir():
                    from warnlive.migrate.tx_historical_source import project as project_tx_historical

                    current_tx_ids = {
                        row["source_identity"].removeprefix("TX:")
                        for row in conn.execute(
                            "SELECT source_identity FROM notices WHERE state='TX' AND source_identity IS NOT NULL")
                        if row["source_identity"].startswith("TX:")
                    }
                    current_tx_events = set()
                    for row in conn.execute(
                        "SELECT n.employer_name,n.notice_date,v.fields_json FROM notices n "
                        "JOIN notice_versions v ON v.notice_id=n.id AND v.version=n.current_version "
                        "WHERE n.state='TX'"
                    ):
                        fields = json.loads(json.loads(row["fields_json"])["raw_extra"])
                        current_tx_events.add((
                            row["employer_name"].casefold(), row["notice_date"],
                            str(fields.get("CITY_NAME") or "").strip().casefold(),
                            str(fields.get("COUNTY_NAME") or "").strip().casefold(),
                        ))
                    tx_historical_records, tx_historical_held, tx_historical_report = (
                        project_tx_historical(tx_historical_dir, current_tx_ids, current_tx_events)
                    )
                    exceptions.extend(tx_historical_held)
                    tx_historical_ingest = _ingest_groups(
                        conn, {"TX": tx_historical_records}, observed_at)
                    if tx_historical_ingest["new"] != len(tx_historical_records):
                        raise ValueError("Texas historical rows did not produce unique notices")
                mo_annual_report = None
                mo_annual_ingest = {"new": 0}
                mo_historical_report = None
                mo_historical_ingest = {"new": 0}
                if mo_annual_dir.is_dir() or mo_historical_dir.is_dir():
                    mo_existing_events = {
                        (row["employer_name"].casefold(), row["effective_date"])
                        for row in conn.execute(
                            "SELECT employer_name,effective_date FROM notices WHERE state='MO'")
                    }
                    if mo_annual_dir.is_dir():
                        from warnlive.migrate.mo_annual_source import project as project_mo_annual

                        mo_records, mo_held, mo_annual_report = project_mo_annual(
                            mo_annual_dir, mo_existing_events)
                        exceptions.extend(mo_held)
                        mo_annual_ingest = _ingest_groups(conn, {"MO": mo_records}, observed_at)
                        if mo_annual_ingest["new"] != len(mo_records):
                            raise ValueError("Missouri annual rows did not produce unique notices")
                        mo_existing_events.update(
                            (row["employer_name"].casefold(), row["effective_date"])
                            for row in mo_records)
                    if mo_historical_dir.is_dir():
                        from warnlive.migrate.mo_historical_source import project as project_mo_historical

                        mo_records, mo_held, mo_historical_report = project_mo_historical(
                            mo_historical_dir, mo_existing_events)
                        exceptions.extend(mo_held)
                        mo_historical_ingest = _ingest_groups(conn, {"MO": mo_records}, observed_at)
                        if mo_historical_ingest["new"] != len(mo_records):
                            raise ValueError("Missouri historical rows did not produce unique notices")
                oh_annual_report = None
                oh_annual_ingest = {"new": 0}
                if oh_annual_dir.is_dir():
                    from warnlive.migrate.oh_annual_source import project as project_oh_annual

                    oh_existing_ids = {
                        row["source_identity"] for row in conn.execute(
                            "SELECT source_identity FROM notices WHERE state='OH' "
                            "AND source_identity IS NOT NULL")
                    }
                    oh_records, oh_held, oh_annual_report = project_oh_annual(
                        oh_annual_dir, oh_existing_ids)
                    exceptions.extend(oh_held)
                    oh_annual_ingest = _ingest_groups(conn, {"OH": oh_records}, observed_at)
                    if oh_annual_ingest["new"] != len(oh_records):
                        raise ValueError("Ohio annual rows did not produce unique notices")
                ga_source_report = None
                ga_ingest = {"new": 0}
                if ga_archive_dir.is_dir():
                    from warnlive.migrate.ga_archive_source import project as project_ga

                    ga_existing_ids = {row["source_identity"].removeprefix("GA:")
                                       for row in conn.execute(
                                           "SELECT source_identity FROM notices WHERE state='GA' AND source_identity IS NOT NULL")
                                       if row["source_identity"].startswith("GA:")}
                    ga_existing_events = {(row["employer_name"].casefold(), row["effective_date"])
                                          for row in conn.execute(
                                              "SELECT employer_name,effective_date FROM notices WHERE state='GA' AND source_identity IS NULL")}
                    ga_records, ga_held, ga_source_report = project_ga(
                        ga_archive_dir, ga_existing_ids, ga_existing_events)
                    exceptions.extend(ga_held)
                    ga_ingest = _ingest_groups(conn, {"GA": ga_records}, observed_at)
                    if ga_ingest["new"] != len(ga_records):
                        raise ValueError("Georgia archive rows did not produce unique notices")
                if ny_annual_dir.is_dir():
                    from warnlive.migrate.ny_annual_source import project as project_ny_annual

                    existing_ny = {row["employer_name"] for row in conn.execute(
                        "SELECT employer_name FROM notices WHERE state='NY'")}
                    existing_ny_sites = {
                        (row["location"].casefold().strip(), row["effective_date"], row["employees_affected"])
                        for row in conn.execute(
                            "SELECT location,effective_date,employees_affected FROM notices WHERE state='NY'")
                        if row["location"] and row["effective_date"] and row["employees_affected"] is not None
                    }
                    existing_ny_notices = [dict(row) for row in conn.execute(
                        "SELECT id, employer_name, location, notice_date, effective_date, "
                        "employees_affected, source_notice_id FROM notices WHERE state='NY'")]
                    ny_records, ny_held, ny_source_report = project_ny_annual(
                        ny_annual_dir, existing_ny, existing_ny_sites,
                        existing_notices=existing_ny_notices)
                    exceptions.extend(ny_held)
                ny_ingest = _ingest_groups(conn, {"NY": ny_records}, observed_at)
                if ny_source_report is not None and ny_ingest["new"] != len(ny_records):
                    raise ValueError("New York dashboard rows did not produce unique notices")
            tx_evidence = None
            tx_dir = source / "agency/tx"
            if source_only and tx_dir.is_dir():
                from warnlive.migrate.tx_source import apply_date_evidence

                tx_evidence = apply_date_evidence(
                    conn, tx_dir, source / "raw/tx.csv", observed_at, exceptions,
                )
            conn.commit()
            il_repair = _repair_il_from_cache(
                out_db, source / "cache/il_reports", observed_at,
            )
            link_report = links_mod.rebuild(conn)
            observation_report = None
            if source_only:
                from warnlive.store.observations import store_observations
                from warnlive.migrate import la_overlay

                ia_notice_ids = {
                    row["source_notice_id"]: conn.execute(
                        "SELECT id FROM notices WHERE source_identity=?",
                        (row["source_identity"],)).fetchone()[0]
                    for row in ia_records
                }
                admission = {}
                for row in ia_current_rows + ia_historical_rows:
                    pointer = row["source_row"]
                    target = ia_related.get(pointer, pointer)
                    if target in ia_notice_ids:
                        admission[pointer] = ("admitted", ia_notice_ids[target])
                    else:
                        admission[pointer] = ("identity_unresolved", None)
                for row in ky_source_rows:
                    source_id = str(row["raw"]["Notice: Notice Number"]).strip()
                    if row["raw"]["County"].strip() == "Out of the State County":
                        admission[row["source_row"]] = ("identity_unresolved", None)
                    else:
                        matches = conn.execute(
                            "SELECT id FROM notices WHERE source_identity = ?",
                            (f"KY:{source_id.removeprefix('Notice ').strip()}",),
                        ).fetchall()
                        if len(matches) != 1:
                            raise ValueError(f"Kentucky observation has no unique notice: {source_id}")
                        admission[row["source_row"]] = ("admitted", matches[0]["id"])
                for row in la_source_rows:
                    pointer = row["source_row"]
                    if row["kind"] == "annotation":
                        admission[pointer] = ("annotation", None)
                    elif row.get("status") == "rescinded":
                        admission[pointer] = ("rescinded", None)
                    elif pointer in la_overlay.HELD:
                        admission[pointer] = ("event_unresolved", None)
                    elif pointer in la_overlay.EMPLOYERS:
                        matches = conn.execute(
                            "SELECT id FROM notices WHERE source_identity = ?",
                            (f"LA:official:{pointer}",),
                        ).fetchall()
                        if len(matches) != 1:
                            raise ValueError(f"Louisiana observation has no unique notice: {pointer}")
                        admission[pointer] = ("admitted", matches[0]["id"])
                    else:
                        raise ValueError(f"unclassified Louisiana observation: {pointer}")
                observation_report = store_observations(
                    conn, ia_current_rows + ia_historical_rows + ky_source_rows + la_source_rows,
                    admission, source_bundle_sha256,
                )
                conn.commit()
            report = {
                "source_files": len(manifest["files"]),
                "source_bytes": sum(item["size"] for item in manifest["files"]),
                "companion_evidence": manifest.get("companion_evidence"),
                "policy": "agency-only-v1",
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
                    "ingested_rows": la_ingest["new"] if source_only else 0,
                    "held_notice_rows": sum(row.get("source_row") in la_overlay.HELD
                                            for row in la_source_rows) if source_only and la_source_rows else 0,
                },
                "ia_official_source": {
                    "current_event_log_rows": len(ia_current_rows),
                    "historical_log_rows": len(ia_historical_rows),
                    "held_rows": ia_source_report["held"] if ia_source_report else 0,
                    "ingested_rows": ia_ingest["new"] if source_only else 0,
                    "hold_reasons": ia_source_report["hold_reasons"] if ia_source_report else {},
                },
                "ky_official_source": ({**ky_source_report, "ingested_rows": ky_ingest["new"]}
                                       if ky_source_report is not None else None),
                "or_official_source": ({**or_source_report, "ingested_rows": or_ingest["new"]}
                                       if or_source_report is not None else None),
                "or_historical_source": ({**or_historical_report,
                                          "ingested_rows": or_historical_ingest["new"]}
                                         if or_historical_report is not None else None),
                "tx_historical_source": ({**tx_historical_report,
                                          "ingested_rows": tx_historical_ingest["new"]}
                                         if tx_historical_report is not None else None),
                "mo_annual_source": ({**mo_annual_report,
                                      "ingested_rows": mo_annual_ingest["new"]}
                                     if mo_annual_report is not None else None),
                "mo_historical_source": ({**mo_historical_report,
                                          "ingested_rows": mo_historical_ingest["new"]}
                                         if mo_historical_report is not None else None),
                "oh_annual_source": ({**oh_annual_report,
                                      "ingested_rows": oh_annual_ingest["new"]}
                                     if oh_annual_report is not None else None),
                "tn_official_source": ({**tn_source_report, "ingested_rows": tn_ingest["new"]}
                                       if tn_source_report is not None else None),
                "ny_official_source": ({**ny_source_report, "ingested_rows": ny_ingest["new"]}
                                       if ny_source_report is not None else None),
                "ga_official_archive": ({**ga_source_report, "ingested_rows": ga_ingest["new"]}
                                        if ga_source_report is not None else None),
                "cached_agencies": agencies,
                "il_effective_repair": il_repair,
                "tx_annual_date_evidence": tx_evidence,
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
                report["source_row_accounting"] = _verify_source_row_accounting(
                    report, exceptions,
                )
                report["exceptions"] = _write_exceptions(exceptions_path, exceptions)
                report["source_observations"] = observation_report
                report["admitted_coverage"] = _admitted_coverage(conn)
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
    parser.add_argument("--source-only", action="store_true", default=True,
                        help="Build from agency sources only (the default)")
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
