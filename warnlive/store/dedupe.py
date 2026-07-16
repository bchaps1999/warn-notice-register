"""Versioned ingest: dedupe-key matching, amendment tracking, first/last_seen."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

# Canonical fields whose values define a version. Order matters for hashing.
VERSIONED_FIELDS = [
    "state",
    "employer_name",
    "location",
    "notice_date",
    "effective_date",
    "employees_affected",
    "layoff_type",
    "is_temporary",
    "is_amendment",
]


@dataclass
class IngestStats:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


def ingest(
    conn: sqlite3.Connection,
    records: list[dict],
    observed_at: str,
) -> IngestStats:
    """Ingest normalized records for one state.

    Per record (which carries dedupe_key and raw_record_hash from the
    normalizer):
      - unknown key            -> insert notice + version 1
      - known key, same hash   -> advance last_seen only (idempotent)
      - known key, new hash    -> add a version, update denormalized fields
    Records absent from the source are untouched; their last_seen simply
    stops advancing.

    Within a single batch, duplicate keys are collapsed to the first
    occurrence (sources sometimes list a notice twice verbatim).
    """
    stats = IngestStats()
    seen_in_batch: set[str] = set()
    cur = conn.cursor()

    for rec in records:
        key = rec["dedupe_key"]
        if key in seen_in_batch:
            continue
        seen_in_batch.add(key)

        row = cur.execute(
            "SELECT n.id AS id, n.current_version AS current_version, "
            "       v.raw_record_hash AS current_hash "
            "FROM notices n JOIN notice_versions v "
            "  ON v.notice_id = n.id AND v.version = n.current_version "
            "WHERE n.dedupe_key = ?",
            (key,),
        ).fetchone()

        if row is None:
            cur.execute(
                """INSERT INTO notices
                   (dedupe_key, state, employer_name, location, notice_date,
                    effective_date, employees_affected, layoff_type, is_temporary,
                    is_amendment, source_url, source_notice_id, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    rec["state"],
                    rec["employer_name"],
                    rec["location"],
                    rec["notice_date"],
                    rec["effective_date"],
                    rec["employees_affected"],
                    rec["layoff_type"],
                    rec["is_temporary"],
                    rec["is_amendment"],
                    rec["source_url"],
                    rec["source_notice_id"],
                    observed_at,
                    observed_at,
                ),
            )
            _insert_version(cur, cur.lastrowid, 1, rec, observed_at)
            stats.new += 1
        elif row["current_hash"] == rec["raw_record_hash"]:
            cur.execute(
                "UPDATE notices SET last_seen = ? WHERE id = ?",
                (observed_at, row["id"]),
            )
            stats.unchanged += 1
        else:
            next_version = row["current_version"] + 1
            _insert_version(cur, row["id"], next_version, rec, observed_at)
            cur.execute(
                """UPDATE notices SET
                     employer_name=?, location=?, notice_date=?, effective_date=?,
                     employees_affected=?, layoff_type=?, is_temporary=?,
                     is_amendment=?, source_url=?, source_notice_id=?,
                     is_amended=1, current_version=?, last_seen=?
                   WHERE id=?""",
                (
                    rec["employer_name"],
                    rec["location"],
                    rec["notice_date"],
                    rec["effective_date"],
                    rec["employees_affected"],
                    rec["layoff_type"],
                    rec["is_temporary"],
                    rec["is_amendment"],
                    rec["source_url"],
                    rec["source_notice_id"],
                    next_version,
                    observed_at,
                    row["id"],
                ),
            )
            stats.updated += 1

    conn.commit()
    return stats


def _insert_version(cur, notice_id, version, rec, observed_at):
    fields = {f: rec[f] for f in VERSIONED_FIELDS}
    fields["raw_extra"] = rec.get("raw_extra")
    cur.execute(
        "INSERT INTO notice_versions (notice_id, version, raw_record_hash, fields_json, observed_at) "
        "VALUES (?,?,?,?,?)",
        (
            notice_id,
            version,
            rec["raw_record_hash"],
            json.dumps(fields, sort_keys=True),
            observed_at,
        ),
    )
