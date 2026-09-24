"""Read-only pilot of dated entity evidence against a WARN candidate database.

Example: python -m warnlive.enrich.temporal_pilot --db candidate.sqlite \
    --ledger data/reference/temporal/ledger.json \
    --selection data/reference/temporal/selection.json \
    --output-dir output/temporal-entity-pilot
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from warnlive.enrich.temporal import Ledger, RELATIONSHIP_TYPES
from warnlive.verify.timing import has_unresolved_date_review

FIELDS = (
    "state", "source_notice_id", "notice_id", "employer_name", "event",
    "as_of_date", "date_precision", "date_basis", "date_status", "identity_status", "entity_id",
    "identity_assertion_ids", "identity_source_ids", "identity_review_statuses", "relationship_type",
    "relationship_status", "parent_entity_id", "relationship_assertion_ids",
    "relationship_source_ids", "relationship_review_statuses",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selection(path: Path) -> list[tuple[str, str]]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError("selection must be a JSON list")
    keys = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("state"), str) or not isinstance(item.get("source_notice_id"), str):
            raise ValueError("each selection needs state and source_notice_id")
        keys.append((item["state"], item["source_notice_id"]))
    if len(keys) != len(set(keys)):
        raise ValueError("selection contains duplicate state/source_notice_id")
    return sorted(keys)


SUPPORTED_BASES = frozenset({"reported", "derived_from_reported_components"})


def _date_status(as_of: str | None, precision: str | None, basis: str | None,
                 review_hold: bool = False) -> str:
    if not as_of:
        return "date_missing"
    if precision is None:
        return "precision_unmarked"
    if precision != "day":
        return "date_not_precise"
    if basis not in SUPPORTED_BASES:
        return "basis_unassessed" if basis is None else "basis_not_source_supported"
    try:
        if len(as_of) != 10 or date.fromisoformat(as_of).isoformat() != as_of:
            return "date_not_precise"
    except ValueError:
        return "date_not_precise"
    if review_hold:
        return "date_review_hold"
    return "day_confirmed"


def _notices(db_path: Path, keys: list[tuple[str, str]]) -> list[sqlite3.Row]:
    uri = "file:" + quote(str(db_path.resolve())) + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notices)")}
        start_precision = ("effective_date_precision" if "effective_date_precision" in columns
                           else "NULL AS effective_date_precision")
        end_precision = ("effective_date_end_precision" if "effective_date_end_precision" in columns
                         else "NULL AS effective_date_end_precision")
        notice_basis = ("notice_date_basis" if "notice_date_basis" in columns
                        else "NULL AS notice_date_basis")
        start_basis = ("effective_date_basis" if "effective_date_basis" in columns
                       else "NULL AS effective_date_basis")
        end_basis = ("effective_date_end_basis" if "effective_date_end_basis" in columns
                     else "NULL AS effective_date_end_basis")
        source_details = ("source_details" if "source_details" in columns
                          else "NULL AS source_details")
        rows = []
        for state, source_notice_id in keys:
            matches = conn.execute(
                "SELECT id, state, source_notice_id, employer_name, notice_date, "
                "effective_date, effective_date_end, notice_date_precision, "
                f"{start_precision}, {end_precision}, {notice_basis}, {start_basis}, "
                f"{end_basis}, {source_details} "
                "FROM notices WHERE state = ? AND source_notice_id = ? ORDER BY id",
                (state, source_notice_id),
            ).fetchall()
            if not matches:
                raise ValueError(f"candidate notice absent: {state} {source_notice_id}")
            if len(matches) != 1:
                raise ValueError(f"candidate notice ambiguous: {state} {source_notice_id}")
            rows.extend(matches)
        return rows
    finally:
        conn.close()


def _snapshot(source_path: Path, snapshot_path: Path) -> str:
    """Freeze one consistent SQLite view, including committed WAL pages.

    Hashing only the main database file can miss WAL-backed revisions that
    readers see. SQLite's backup API captures a transactionally consistent
    database; every pilot row is then read from those exact hashed bytes.
    """
    uri = "file:" + quote(str(source_path.resolve())) + "?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    target = sqlite3.connect(snapshot_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return _sha256(snapshot_path)


def evaluate(ledger: Ledger, notices: list[sqlite3.Row]) -> list[dict]:
    """Generate explicit results for each of the three date events and five relationship types."""
    output = []
    for notice in notices:
        try:
            details = json.loads(notice["source_details"] or "{}")
        except (TypeError, ValueError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        review_hold = has_unresolved_date_review(details)
        try:
            start = date.fromisoformat(notice["effective_date"])
            end = date.fromisoformat(notice["effective_date_end"])
            review_hold = review_hold or end < start
        except (TypeError, ValueError):
            pass
        identity = ledger.identity(notice["source_notice_id"], notice["state"], notice["employer_name"])
        events = (
            ("notice", notice["notice_date"], notice["notice_date_precision"], notice["notice_date_basis"]),
            ("effective_start", notice["effective_date"], notice["effective_date_precision"], notice["effective_date_basis"]),
            ("effective_end", notice["effective_date_end"], notice["effective_date_end_precision"], notice["effective_date_end_basis"]),
        )
        for event, as_of, precision, basis in events:
            date_status = _date_status(as_of, precision, basis, review_hold)
            for relationship_type in RELATIONSHIP_TYPES:
                status = None
                parent = None
                relationship_assertions: tuple[str, ...] = ()
                relationship_sources: tuple[str, ...] = ()
                relationship_reviews: tuple[str, ...] = ()
                if identity.status != "accepted":
                    status = "identity_conflict" if identity.status == "conflict" else "identity_unresolved"
                elif date_status == "date_missing":
                    status = "date_missing"
                elif date_status == "date_review_hold":
                    status = "date_review_hold"
                elif date_status != "day_confirmed":
                    status = "date_not_precise"
                else:
                    result = ledger.relationship(identity.entity_id or "", relationship_type, as_of)
                    status = result.status
                    parent = result.parent_entity_id
                    relationship_assertions = result.assertion_ids
                    relationship_sources = result.source_ids
                    relationship_reviews = result.review_statuses
                output.append({
                    "state": notice["state"],
                    "source_notice_id": notice["source_notice_id"],
                    "notice_id": notice["id"],
                    "employer_name": notice["employer_name"],
                    "event": event,
                    "as_of_date": as_of or "",
                    "date_precision": precision or "unknown",
                    "date_basis": basis or "unassessed",
                    "date_status": date_status,
                    "identity_status": identity.status,
                    "entity_id": identity.entity_id or "",
                    "identity_assertion_ids": "|".join(identity.assertion_ids),
                    "identity_source_ids": "|".join(identity.source_ids),
                    "identity_review_statuses": "|".join(identity.review_statuses),
                    "relationship_type": relationship_type,
                    "relationship_status": status,
                    "parent_entity_id": parent or "",
                    "relationship_assertion_ids": "|".join(relationship_assertions),
                    "relationship_source_ids": "|".join(relationship_sources),
                    "relationship_review_statuses": "|".join(relationship_reviews),
                })
    return output


def run(db_path: Path, ledger_path: Path, selection_path: Path, output_dir: Path) -> dict:
    """Write stable sidecars without opening the candidate database for writes."""
    ledger = Ledger(ledger_path)
    keys = _selection(selection_path)
    with TemporaryDirectory(prefix="warn-temporal-snapshot-") as temp_dir:
        snapshot_path = Path(temp_dir) / "candidate.sqlite"
        snapshot_sha256 = _snapshot(db_path, snapshot_path)
        notices = _notices(snapshot_path, keys)
    rows = evaluate(ledger, notices)
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "temporal_entity_review.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "schema_version": 1,
        "candidate_db_sha256": snapshot_sha256,
        "candidate_hash_basis": "sqlite_backup_snapshot_including_committed_wal",
        "resolver_code_sha256": _sha256(Path(__file__).with_name("temporal.py")),
        "pilot_code_sha256": _sha256(Path(__file__)),
        "ledger_sha256": ledger.sha256,
        "selection_sha256": _sha256(selection_path),
        "review_csv_sha256": _sha256(csv_path),
        "selected_notices": len(notices),
        "review_rows": len(rows),
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.db, args.ledger, args.selection, args.output_dir), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
