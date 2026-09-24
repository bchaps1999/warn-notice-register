"""Guarded Kansas worker-count evidence from a prior agency capture.

The current Kansas table retains stable record numbers but omits some worker
counts that the preserved historical table reported for those same records.
Only an exact record-number, employer, and notice-day match may fill a blank.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from warnlive.store.dedupe import append_repair_version


def apply_prior_worker_counts(
    conn: sqlite3.Connection, current_records: list[dict],
    archive_records: list[dict], observed_at: str,
) -> dict[str, int]:
    """Preserve a prior agency count when the same current record leaves it blank.

    The archive is evidence for the count only. It does not replace current
    names, dates, locations, or URLs, and every changed canonical row gets a
    version with a pointer to the archived source row.
    """
    def keyed(records: list[dict], label: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for rec in records:
            identity = rec.get("source_identity")
            if not identity or not identity.startswith("KS:"):
                raise ValueError(f"Kansas {label} row lacks a stable record number")
            if identity in out:
                raise ValueError(f"duplicate Kansas {label} record number: {identity}")
            out[identity] = rec
        return out

    current = keyed(current_records, "current")
    archive = keyed(archive_records, "archive")
    changes: list[tuple[int, int, str, int, str]] = []
    for identity in sorted(current):
        now = current[identity]
        older = archive.get(identity)
        if older is None:
            continue
        if (now["employer_name"], now["notice_date"]) != (
            older["employer_name"], older["notice_date"]
        ):
            raise ValueError(f"Kansas record changed employer or notice day: {identity}")
        current_count = now["employees_affected"]
        older_count = older["employees_affected"]
        if current_count is not None:
            if older_count is not None and current_count != older_count:
                raise ValueError(f"Kansas worker count conflict: {identity}")
            continue
        if older_count is None:
            continue
        if not isinstance(older_count, int) or older_count < 0:
            raise ValueError(f"invalid prior Kansas worker count: {identity}")
        matches = conn.execute(
            "SELECT id, employer_name, notice_date, source_details, is_amended, "
            "employees_affected FROM notices WHERE dedupe_key = ? AND source_identity = ?",
            (now["dedupe_key"], identity),
        ).fetchall()
        if len(matches) != 1:
            raise ValueError(f"Kansas current row is not unique: {identity}")
        row = matches[0]
        if (row["employer_name"], row["notice_date"]) != (
            now["employer_name"], now["notice_date"]
        ):
            raise ValueError(f"Kansas candidate drift: {identity}")
        details = json.loads(row["source_details"] or "{}")
        evidence = {
            "basis": "prior_agency_capture",
            "artifact": "backfill/raw/ks.csv",
            "source_record_number": identity.removeprefix("KS:"),
            "source_row_sha256": hashlib.sha256(older["raw_extra"].encode()).hexdigest(),
            "reported_workers": older_count,
            "current_source_value": None,
        }
        if row["employees_affected"] == older_count and details.get("worker_count_evidence") == evidence:
            continue
        if row["employees_affected"] is not None:
            raise ValueError(f"Kansas candidate worker count drift: {identity}")
        details["worker_count_evidence"] = evidence
        changes.append((
            row["id"], older_count,
            json.dumps(details, sort_keys=True, ensure_ascii=False),
            row["is_amended"], identity,
        ))

    if not changes:
        return {"filled_rows": 0, "restored_reported_workers": 0}

    # Apply only after every source and candidate row passes preflight. The
    # savepoint also rolls back if versioning fails during the write loop.
    started_transaction = not conn.in_transaction
    if started_transaction:
        conn.execute("BEGIN")
    conn.execute("SAVEPOINT ks_prior_worker_counts")
    try:
        for notice_id, count, details_json, is_amended, identity in changes:
            conn.execute(
                "UPDATE notices SET employees_affected = ?, source_details = ? WHERE id = ?",
                (count, details_json, notice_id),
            )
            if not append_repair_version(conn, notice_id, observed_at):
                raise ValueError(f"Kansas repair did not create a version: {identity}")
            # This interpretation is not an employer-filed amendment.
            conn.execute(
                "UPDATE notices SET is_amended = ? WHERE id = ?",
                (is_amended, notice_id),
            )
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT ks_prior_worker_counts")
        conn.execute("RELEASE SAVEPOINT ks_prior_worker_counts")
        if started_transaction:
            conn.rollback()
        raise
    conn.execute("RELEASE SAVEPOINT ks_prior_worker_counts")
    return {
        "filled_rows": len(changes),
        "restored_reported_workers": sum(item[1] for item in changes),
    }
