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

# Optional v5 facts. Old records omit them and retain their original hashes;
# newly extracted evidence changes the semantic version even when scalar
# start date, workers and employer happen to stay the same.
DETAIL_FIELDS = [
    "effective_date_end", "notice_date_precision", "notice_date_basis",
    "source_identity", "source_details",
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
    *,
    commit: bool = True,
) -> IngestStats:
    """Ingest normalized records for one state.

    Per record (which carries dedupe_key and raw_record_hash from the
    normalizer):
      - unknown key            -> insert notice + version 1
      - known key, same hash   -> idempotent no-op (unfreezes last_seen)
      - known key, new hash    -> add a version, update denormalized fields

    last_seen is NULL while a notice is present in its source: "still there
    as of the state's latest run" is one fact about the run, not one fact
    per row, and stamping a date on every observed row rewrote most of the
    database (and every export) every day for no information. freeze_absent
    stamps a date only when a notice disappears — which is the moment the
    column actually learns something.

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
            "       n.source_url AS source_url, n.last_seen AS last_seen, "
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
                    effective_date, effective_date_end, notice_date_precision,
                    notice_date_basis, source_identity, source_details,
                    employees_affected, layoff_type, is_temporary,
                    is_amendment, source_url, source_notice_id, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    key,
                    rec["state"],
                    rec["employer_name"],
                    rec["location"],
                    rec["notice_date"],
                    rec["effective_date"],
                    rec.get("effective_date_end"),
                    rec.get("notice_date_precision"),
                    rec.get("notice_date_basis"),
                    rec.get("source_identity"),
                    rec.get("source_details"),
                    rec["employees_affected"],
                    rec["layoff_type"],
                    rec["is_temporary"],
                    rec["is_amendment"],
                    rec["source_url"],
                    rec["source_notice_id"],
                    observed_at,
                    None,
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
                    "UPDATE notices SET last_seen = NULL, source_url = ?, "
                    "source_notice_id = ? WHERE id = ?",
                    (url, rec.get("source_notice_id"), row["id"]),
                )
            elif row["last_seen"] is not None:
                # A frozen notice reappeared (or predates NULL-means-current):
                # unfreeze it. Written once, not every day.
                cur.execute(
                    "UPDATE notices SET last_seen = NULL WHERE id = ?",
                    (row["id"],),
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
            if row["last_seen"] is not None:
                cur.execute(
                    "UPDATE notices SET last_seen = NULL WHERE id = ?",
                    (row["id"],),
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
                     effective_date_end=?, notice_date_precision=?,
                     notice_date_basis=?, source_identity=?, source_details=?,
                     employees_affected=?, layoff_type=?, is_temporary=?,
                     is_amendment=?, source_url=?, source_notice_id=?,
                     is_amended=1, current_version=?, last_seen=NULL
                   WHERE id=?""",
                (
                    rec["employer_name"],
                    rec["location"],
                    rec["notice_date"],
                    rec["effective_date"],
                    rec.get("effective_date_end"),
                    rec.get("notice_date_precision"),
                    rec.get("notice_date_basis"),
                    rec.get("source_identity"),
                    rec.get("source_details"),
                    rec["employees_affected"],
                    rec["layoff_type"],
                    rec["is_temporary"],
                    rec["is_amendment"],
                    rec["source_url"],
                    rec["source_notice_id"],
                    next_version,
                    row["id"],
                ),
            )
            stats.updated += 1

    if commit:
        conn.commit()
    return stats


def freeze_absent(
    conn: sqlite3.Connection,
    state: str,
    present_keys: set[str],
    frozen_at: str,
    *,
    commit: bool = True,
) -> int:
    """Stamp last_seen on the state's notices that left its source.

    Called after a full live fetch of a state, never after a backfill — a
    historical batch is not the current state of the source, and freezing
    against one would mark everything it doesn't mention as gone.

    frozen_at should be the date of the *previous* successful run: that is
    the last date the notice was actually observed. The diff runs in Python
    because a state's key set can exceed SQLite's bound-parameter limit.
    """
    current = conn.execute(
        "SELECT id, dedupe_key FROM notices WHERE state = ? AND last_seen IS NULL",
        (state.upper(),),
    ).fetchall()
    gone = [r["id"] for r in current if r["dedupe_key"] not in present_keys]
    for start in range(0, len(gone), 500):
        chunk = gone[start:start + 500]
        conn.execute(
            "UPDATE notices SET last_seen = ? WHERE id IN (%s)"
            % ",".join("?" * len(chunk)),
            (frozen_at, *chunk),
        )
    if gone:
        if commit:
            conn.commit()
        logger.info(
            "dedupe: %s: %d notices left the source; last_seen frozen at %s",
            state.upper(), len(gone), frozen_at,
        )
    return len(gone)


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
    fields.update({f: rec[f] for f in DETAIL_FIELDS if rec.get(f) is not None})
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


def append_repair_version(
    conn: sqlite3.Connection, notice_id: int, observed_at: str,
) -> bool:
    """Version a deliberate canonical repair already applied to notices.

    This is not source ingestion: the source row and its raw_extra remain the
    same, while a corrected canonical interpretation becomes the next
    auditable version. Caller owns the surrounding transaction and key/link
    collision policy.
    """
    from warnlive.normalize.engine import _record_hash

    row = conn.execute(
        "SELECT n.*, v.fields_json, v.raw_record_hash AS current_hash "
        "FROM notices n JOIN notice_versions v "
        "ON v.notice_id = n.id AND v.version = n.current_version "
        "WHERE n.id = ?", (notice_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"notice {notice_id} has no current version")
    previous = json.loads(row["fields_json"])
    rec = {field: row[field] for field in VERSIONED_FIELDS}
    rec.update({field: row[field] for field in DETAIL_FIELDS if row[field] is not None})
    rec["raw_extra"] = previous.get("raw_extra")
    rec["raw_record_hash"] = _record_hash(rec)
    if rec["raw_record_hash"] == row["current_hash"]:
        return False
    next_version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM notice_versions WHERE notice_id = ?",
        (notice_id,),
    ).fetchone()[0]
    _insert_version(conn.cursor(), notice_id, next_version, rec, observed_at)
    conn.execute(
        "UPDATE notices SET current_version = ?, is_amended = 1 WHERE id = ?",
        (next_version, notice_id),
    )
    return True
