"""Versioned ingest: dedupe-key matching, amendment tracking, first/last_seen."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

logger = logging.getLogger("warnlive")

# Two rows under one key whose effective dates sit further apart than this
# are probably not one notice amended but two notices the key could not
# tell apart — which happens exactly where notice_date is null and the key
# runs out of fields. Counted and logged, never merged silently.
COLLISION_WINDOW_DAYS = 45

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
    #: updates whose effective dates disagree beyond COLLISION_WINDOW_DAYS —
    #: likely two distinct notices sharing a key, not an amendment.
    suspected_collisions: int = 0


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

    Within a single batch, a duplicate key with the *same* hash is collapsed
    to the first occurrence (sources sometimes list a notice twice verbatim).
    A duplicate key with a different hash is a different row — states append
    amendment rows rather than editing — and goes through the update path,
    so the later values become the current version instead of being dropped.
    """
    stats = IngestStats()
    seen_in_batch: dict[str, str] = {}
    cur = conn.cursor()

    for rec in records:
        key = rec["dedupe_key"]
        if seen_in_batch.get(key) == rec["raw_record_hash"]:
            continue
        seen_in_batch[key] = rec["raw_record_hash"]

        row = cur.execute(
            "SELECT n.id AS id, n.current_version AS current_version, "
            "       n.effective_date AS effective_date, "
            "       n.source_url AS source_url, "
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
            # The row is unchanged, but where it was seen may not be: a
            # notice first ingested from a Wayback backfill keeps pointing
            # at the archive forever unless the live scraper's URL replaces
            # it. Refreshed only in the live direction — an archive URL
            # never displaces a live one.
            url = rec.get("source_url")
            if (
                url and url != row["source_url"]
                and not ("web.archive.org" in url
                         and row["source_url"]
                         and "web.archive.org" not in row["source_url"])
            ):
                cur.execute(
                    "UPDATE notices SET last_seen = ?, source_url = ?, "
                    "source_notice_id = ? WHERE id = ?",
                    (observed_at, url, rec.get("source_notice_id"), row["id"]),
                )
            else:
                cur.execute(
                    "UPDATE notices SET last_seen = ? WHERE id = ?",
                    (observed_at, row["id"]),
                )
            stats.unchanged += 1
        elif cur.execute(
            "SELECT 1 FROM notice_versions WHERE notice_id = ? AND raw_record_hash = ?",
            (row["id"], rec["raw_record_hash"]),
        ).fetchone():
            # Not the current version, but one we already hold. A source that
            # lists a notice twice — original row, then amendment — re-sends
            # both every day; treating the original as "new again" would
            # ping-pong two junk versions per key per run, forever. Seen
            # before means seen, whichever version it was.
            cur.execute(
                "UPDATE notices SET last_seen = ? WHERE id = ?",
                (observed_at, row["id"]),
            )
            stats.unchanged += 1
        else:
            if _dates_disagree(row["effective_date"], rec["effective_date"]):
                stats.suspected_collisions += 1
                logger.warning(
                    "dedupe: key %s updated with an effective date %s -> %s "
                    "further than %d days apart — likely two distinct notices "
                    "sharing a key",
                    key, row["effective_date"], rec["effective_date"],
                    COLLISION_WINDOW_DAYS,
                )
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


def _dates_disagree(a: str | None, b: str | None) -> bool:
    """Whether two effective dates sit further apart than an amendment moves."""
    if not a or not b:
        return False
    try:
        da, db = date.fromisoformat(a[:10]), date.fromisoformat(b[:10])
    except ValueError:
        return False
    return abs((da - db).days) > COLLISION_WINDOW_DAYS


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
