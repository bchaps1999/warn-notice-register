"""Apply explicitly reviewed New York date correspondences to a candidate DB.

The caller supplies a writable *isolated* SQLite candidate and decisions made
after filing review. This module never nominates source matches and never opens
the canonical database itself. All decisions are preflighted before mutation.
"""

from __future__ import annotations

import hashlib
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Iterable

from warnlive.migrate.ny_source import read_artifacts
from warnlive.normalize.engine import _dedupe_key
from warnlive.store.dedupe import append_repair_version


REQUIRED = (
    "bln_source_notice_id", "bln_row_sha256", "expected_employer_name",
    "expected_location", "expected_dedupe_key", "expected_notice_date",
    "expected_effective_date", "official_source_row_id", "official_row_sha256",
    "new_notice_date", "new_effective_date",
)
OPTIONAL = ("new_effective_date_end", "official_document_path", "official_document_sha256",
            "event_decision_id", "effective_date_role", "employee_separation_phases",
            "timing_interpretation", "worker_count_policy")
PROVENANCE_KEY = "ny_reviewed_repair"


def _iso(value: str | None, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{field} must be an ISO day or null")


def _raw_sha(raw_extra: str) -> str:
    """Use the same canonical BLN row bytes as ny_reconcile.build."""
    raw = json.loads(raw_extra)
    if not isinstance(raw, dict):
        raise ValueError("BLN raw_extra is not a JSON object")
    encoded = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _provenance(decision: dict, official: dict) -> dict:
    provenance = {
        "bln_source_notice_id": decision["bln_source_notice_id"],
        "bln_row_sha256": decision["bln_row_sha256"],
        "official_source_row_id": official["source_row_id"],
        "official_row_sha256": official["row_sha256"],
        "official_source_url": official["source_url"],
        "official_notice_date": official["notice_date"],
        "official_effective_date": official["effective_date"],
        "previous_notice_date": decision["expected_notice_date"],
        "previous_effective_date": decision["expected_effective_date"],
        "selected_notice_date": decision["new_notice_date"],
        "selected_effective_date": decision["new_effective_date"],
        "selected_effective_date_end": decision.get("new_effective_date_end"),
        "official_document_path": decision.get("official_document_path"),
        "official_document_sha256": decision.get("official_document_sha256"),
    }
    for field in ("event_decision_id", "effective_date_role", "employee_separation_phases",
                  "timing_interpretation", "worker_count_policy"):
        if field in decision:
            provenance[field] = decision[field]
    return provenance


def _verified_document(item: dict, artifact_dir: Path, source_id: str) -> None:
    rel_path = item.get("official_document_path")
    digest = item.get("official_document_sha256")
    if not rel_path and not digest:
        return
    if (not isinstance(rel_path, str) or not rel_path
            or not isinstance(digest, str) or len(digest) != 64):
        raise ValueError(f"incomplete official document pin for {source_id}")
    root = Path(artifact_dir).resolve()
    supplied = root / rel_path
    document = supplied.resolve()
    if (not document.is_relative_to(root) or supplied.is_symlink()
            or not document.is_file()
            or hashlib.sha256(document.read_bytes()).hexdigest() != digest):
        raise ValueError(f"official document drift for {source_id}")


def preflight_reviewed_event_decisions(
    source_csv: Path, artifact_dir: Path, decisions: Iterable[dict], conn: sqlite3.Connection,
) -> dict[str, dict]:
    """Verify both BLN observations and the official row before excluding a duplicate.

    The returned mapping is consumed by the BLN admission and exception ledger.
    This does not mutate the candidate and must run before BLN ingestion.
    """
    decisions = list(decisions)
    if not decisions:
        return {}
    official_rows = {row["source_row_id"]: row for row in read_artifacts(artifact_dir)}
    seen_ids: set[str] = set()
    exclusions: dict[str, dict] = {}
    rows_by_ordinal: dict[int, dict] = {}
    with Path(source_csv).open(newline="") as stream:
        for ordinal, row in enumerate(csv.DictReader(stream), start=1):
            rows_by_ordinal[ordinal] = row
    for decision in decisions:
        if decision.get("decision_kind") != "duplicate_observation_and_date_role_repair":
            raise ValueError("unsupported New York event decision")
        survivor, duplicate = decision["survivor"], decision["duplicate"]
        if survivor["bln_source_notice_id"] == duplicate["bln_source_notice_id"]:
            raise ValueError("New York duplicate points to itself")
        for item in (survivor, duplicate):
            source_id = item["bln_source_notice_id"]
            if source_id in seen_ids:
                raise ValueError(f"duplicate New York event decision source ID: {source_id}")
            seen_ids.add(source_id)
            row = rows_by_ordinal.get(item["bln_source_row"])
            if row is None or row.get("hash_id") != source_id or row.get("postal_code") != "NY":
                raise ValueError(f"New York BLN row identity drift for {source_id}")
            if row.get("is_superseded") == "True" or _raw_sha(json.dumps(row)) != item["bln_row_sha256"]:
                raise ValueError(f"New York BLN raw-row drift for {source_id}")
            expected = (item["expected_employer_name"], item["expected_location"],
                        item["expected_notice_date"], item["expected_effective_date"])
            actual = (row["company"], row["location"] or None,
                      row["notice_date"] or None, row["effective_date"] or None)
            if actual != expected or item["expected_effective_date_end"] is not None:
                raise ValueError(f"New York BLN expected-field drift for {source_id}")
            key = _dedupe_key({"state": "NY", "employer_name": row["company"],
                               "location": row["location"] or None,
                               "notice_date": row["notice_date"] or None})
            if key != item["expected_dedupe_key"]:
                raise ValueError(f"New York BLN key drift for {source_id}")
        official = official_rows.get(decision["official_source_row_id"])
        if official is None or official["row_sha256"] != decision["official_row_sha256"]:
            raise ValueError("New York official source drift for event decision")
        if (official["notice_date"] != decision["selected_notice_date"]
                or official["effective_date"] != decision["selected_effective_date"]
                or decision["selected_effective_date_end"] is not None):
            raise ValueError("New York selected event dates disagree with dashboard")
        _verified_document(decision, artifact_dir, duplicate["bln_source_notice_id"])
        if not decision.get("official_event_number") or not decision.get("decision_id"):
            raise ValueError("New York event decision lacks filing identity")
        if decision.get("worker_count_policy") != "leave_bln_count_missing":
            raise ValueError("New York event decision cannot promote a worker count")
        if conn.execute("SELECT 1 FROM notices WHERE state='NY' AND source_notice_id=?",
                        (duplicate["bln_source_notice_id"],)).fetchone():
            raise ValueError("reviewed New York duplicate was ingested before exclusion")
        exclusions[duplicate["bln_source_notice_id"]] = {
            "source_row": duplicate["bln_source_row"],
            "source_row_sha256": duplicate["bln_row_sha256"],
            "disposition": "reviewed_duplicate",
            "official_source_rows": [decision["official_source_row_id"]],
            "match_basis": "filing_verified_duplicate_observation",
            "survivor_source_notice_id": survivor["bln_source_notice_id"],
            "survivor_expected_dedupe_key": survivor["expected_dedupe_key"],
            "reviewed_decision_id": decision["decision_id"],
        }
    return exclusions


def apply_reviewed_event_repairs(
    conn: sqlite3.Connection, decisions: Iterable[dict], artifact_dir: Path, observed_at: str,
) -> dict:
    """Repair retained observations after duplicate exclusion and regular NY repairs."""
    repairs = []
    for decision in decisions:
        survivor = decision["survivor"]
        repairs.append({
            **{key: survivor[key] for key in REQUIRED[:7]},
            "official_source_row_id": decision["official_source_row_id"],
            "official_row_sha256": decision["official_row_sha256"],
            "new_notice_date": decision["selected_notice_date"],
            "new_effective_date": decision["selected_effective_date"],
            "new_effective_date_end": decision["selected_effective_date_end"],
            "official_document_path": decision["official_document_path"],
            "official_document_sha256": decision["official_document_sha256"],
            "event_decision_id": decision["decision_id"],
            "effective_date_role": decision["effective_date_role"],
            "employee_separation_phases": decision["employee_separation_phases"],
            "timing_interpretation": decision["timing_interpretation"],
            "worker_count_policy": decision["worker_count_policy"],
        })
    return apply_reviewed_repairs(conn, repairs, artifact_dir, observed_at)


def apply_reviewed_repairs(
    conn: sqlite3.Connection,
    decisions: Iterable[dict],
    artifact_dir: Path,
    observed_at: str,
) -> dict:
    """Apply safe decisions atomically; report key collisions without merging.

    ``decisions`` must pin the original BLN row and all expected current
    identity/date fields. A rerun of the same decisions is a no-op. Source or
    expected-field drift aborts the whole batch. Colliding decisions are held
    and listed in the result; non-colliding decisions may still apply.

    The connection must use ``sqlite3.Row`` and point at an isolated candidate.
    The caller controls connection lifetime. This function does not commit an
    outer transaction: a savepoint makes its own changes atomic.
    """
    if conn.row_factory is not sqlite3.Row:
        raise ValueError("candidate connection must use sqlite3.Row")
    if not observed_at or not isinstance(observed_at, str):
        raise ValueError("observed_at is required")
    decisions = list(decisions)
    official_rows = {row["source_row_id"]: row for row in read_artifacts(artifact_dir)}
    seen_source: set[str] = set()
    prepared = []
    for item in decisions:
        if (not isinstance(item, dict) or not set(REQUIRED) <= set(item)
                or set(item) - set(REQUIRED) - set(OPTIONAL)):
            raise ValueError(f"NY decision requires: {', '.join(REQUIRED)}")
        source_id = item["bln_source_notice_id"]
        if not isinstance(source_id, str) or not source_id or source_id in seen_source:
            raise ValueError(f"duplicate or missing BLN source ID: {source_id!r}")
        seen_source.add(source_id)
        for field in ("expected_notice_date", "expected_effective_date",
                      "new_notice_date", "new_effective_date", "new_effective_date_end"):
            if field == "new_effective_date_end" and field not in item:
                continue
            _iso(item[field], field)
        end = item.get("new_effective_date_end")
        if end is not None:
            if item["new_effective_date"] is None or end < item["new_effective_date"]:
                raise ValueError(f"effective end precedes start for {source_id}")
            if not item.get("official_document_path"):
                raise ValueError(f"effective end requires pinned official document for {source_id}")
        _verified_document(item, artifact_dir, source_id)
        official = official_rows.get(item["official_source_row_id"])
        if official is None or official["row_sha256"] != item["official_row_sha256"]:
            raise ValueError(f"official source drift for {source_id}")
        if (item["new_notice_date"] != official["notice_date"]
                or item["new_effective_date"] != official["effective_date"]):
            raise ValueError(f"selected dates do not match official row for {source_id}")
        rows = conn.execute(
            "SELECT n.*, v.fields_json FROM notices n JOIN notice_versions v "
            "ON v.notice_id=n.id AND v.version=n.current_version "
            "WHERE n.state='NY' AND n.source_notice_id=?", (source_id,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(f"expected one current NY notice for {source_id}, found {len(rows)}")
        row = rows[0]
        previous = json.loads(row["fields_json"])
        if _raw_sha(previous.get("raw_extra") or "null") != item["bln_row_sha256"]:
            raise ValueError(f"BLN raw-row drift for {source_id}")
        expected = {
            "employer_name": item["expected_employer_name"],
            "location": item["expected_location"],
            "dedupe_key": item["expected_dedupe_key"],
            "notice_date": item["expected_notice_date"],
            "effective_date": item["expected_effective_date"],
        }
        new_key = _dedupe_key({
            "state": "NY", "employer_name": row["employer_name"],
            "location": row["location"], "notice_date": item["new_notice_date"],
        })
        provenance = _provenance(item, official)
        details = json.loads(row["source_details"] or "{}")
        if not isinstance(details, dict):
            raise ValueError(f"source_details is not an object for {source_id}")
        already_applied = details.get(PROVENANCE_KEY) == provenance
        if already_applied:
            if (row["notice_date"] != item["new_notice_date"]
                    or row["effective_date"] != item["new_effective_date"]
                    or row["effective_date_end"] != end
                    or row["dedupe_key"] != new_key
                    or row["notice_date_precision"] != "day"
                    or row["notice_date_basis"] != "reported"
                    or row["effective_date_precision"] != "day"
                    or row["effective_date_basis"] != "reported"
                    or row["effective_date_end_precision"] != ("day" if end else None)
                    or row["effective_date_end_basis"] != ("reported" if end else None)):
                raise ValueError(f"partially applied NY repair for {source_id}")
        elif any(row[field] != value for field, value in expected.items()):
            raise ValueError(f"expected field drift for {source_id}")
        elif PROVENANCE_KEY in details:
            raise ValueError(f"conflicting NY repair provenance for {source_id}")
        if not already_applied and row["effective_date_end"] is not None:
            raise ValueError(f"NY repair requires blank effective end for {source_id}")
        prepared.append({"id": row["id"], "source_id": source_id,
                         "new_key": new_key, "new_notice_date": item["new_notice_date"],
                         "new_effective_date": item["new_effective_date"],
                         "new_effective_date_end": end,
                         "details": details, "provenance": provenance,
                         "already_applied": already_applied})

    # A proposed key may belong to another active notice, including another
    # decision in this batch. Holding all proposals for that key avoids a
    # silent merge, swap, or outcome that depends on input order.
    by_key: dict[str, list[dict]] = defaultdict(list)
    for item in prepared:
        by_key[item["new_key"]].append(item)
    held = {}
    for key, proposals in sorted(by_key.items()):
        owner = conn.execute("SELECT id FROM notices WHERE dedupe_key=?", (key,)).fetchone()
        proposed_ids = {item["id"] for item in proposals}
        if len(proposals) > 1 or (owner is not None and owner["id"] not in proposed_ids):
            for item in proposals:
                held[item["source_id"]] = {
                    "proposed_dedupe_key": key,
                    "existing_notice_id": owner["id"] if owner else None,
                    "convergent_source_ids": sorted(p["source_id"] for p in proposals),
                }
    report = {"applied": [], "already_applied": [], "held_collisions": held}
    conn.execute("SAVEPOINT ny_reviewed_repair")
    try:
        for item in prepared:
            source_id = item["source_id"]
            if source_id in held:
                continue
            if item["already_applied"]:
                report["already_applied"].append(source_id)
                continue
            details = dict(item["details"])
            details[PROVENANCE_KEY] = item["provenance"]
            conn.execute(
                "UPDATE notices SET notice_date=?, effective_date=?, effective_date_end=?, dedupe_key=?, "
                "source_details=?, notice_date_precision='day', notice_date_basis='reported', "
                "effective_date_precision='day', effective_date_basis='reported', "
                "effective_date_end_precision=?, effective_date_end_basis=? "
                "WHERE id=?",
                (item["new_notice_date"], item["new_effective_date"],
                 item["new_effective_date_end"], item["new_key"],
                 json.dumps(details, sort_keys=True, ensure_ascii=False),
                 "day" if item["new_effective_date_end"] else None,
                 "reported" if item["new_effective_date_end"] else None,
                 item["id"]),
            )
            if not append_repair_version(conn, item["id"], observed_at):
                raise ValueError(f"repair produced no version for {source_id}")
            report["applied"].append(source_id)
        conn.execute("RELEASE SAVEPOINT ny_reviewed_repair")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT ny_reviewed_repair")
        conn.execute("RELEASE SAVEPOINT ny_reviewed_repair")
        raise
    return report
