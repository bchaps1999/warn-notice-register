"""Read-only evidence inventory for Georgia and Iowa historical migrations.

Matches here are *candidates*, never instructions to re-key, split, or delete a
notice.  In particular a Georgia WARN ID proves filing membership but not
which historical version is current; an Iowa raw-row match says nothing about
whether another row is a phase or an amendment.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _clean(raw: dict) -> dict[str, str]:
    return {str(k): str(v or "").strip() for k, v in raw.items() if k is not None}


def _raw_hash(raw: dict) -> str:
    return _digest(json.dumps(_clean(raw), sort_keys=True, ensure_ascii=False).encode())


def _raw(value: object) -> dict:
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _filing(raw: dict) -> str | None:
    value = str(raw.get("GA WARN ID") or "").strip()
    return f"GA:{value}" if value else None


def reconcile(
    conn: sqlite3.Connection, state: str, source_csv: Path,
) -> dict:
    """Return a deterministic, complete version/source evidence report.

    The caller must open SQLite read-only.  Nothing here writes to the DB or
    interprets absent rows in a current snapshot as historical deletions.
    """
    if state not in {"GA", "IA"}:
        raise ValueError("historical reconciliation is limited to GA and IA")
    source_bytes = source_csv.read_bytes()
    with source_csv.open(newline="", encoding="utf-8-sig") as fh:
        source_rows = list(csv.DictReader(fh))
    sources = []
    by_raw: dict[str, list[int]] = defaultdict(list)
    by_filing: dict[str, list[int]] = defaultdict(list)
    for ordinal, raw in enumerate(source_rows, 1):
        item = {
            "ordinal": ordinal, "raw_hash": _raw_hash(raw),
            "filing": _filing(raw) if state == "GA" else None,
            "raw": raw,
        }
        sources.append(item)
        by_raw[item["raw_hash"]].append(ordinal)
        if item["filing"]:
            by_filing[item["filing"]].append(ordinal)

    versions = []
    source_to_versions: dict[int, list[tuple[int, int]]] = defaultdict(list)
    notice_filings: dict[int, set[str]] = defaultdict(set)
    query = """
        SELECT n.id, n.dedupe_key, v.version, v.fields_json
        FROM notices n JOIN notice_versions v ON v.notice_id = n.id
        WHERE n.state = ? ORDER BY n.id, v.version
    """
    for row in conn.execute(query, (state,)):
        fields = _raw(row["fields_json"])
        raw = _raw(fields.get("raw_extra"))
        raw_hash = _raw_hash(raw) if raw else None
        filing = _filing(raw) if state == "GA" else None
        if filing:
            notice_filings[row["id"]].add(filing)
        exact = by_raw.get(raw_hash, []) if raw_hash else []
        filing_candidates = by_filing.get(filing, []) if filing else []
        candidates = exact or filing_candidates
        if not raw:
            status = "missing_original_raw"
        elif len(exact) == 1:
            status = "exact_raw_candidate"
        elif len(exact) > 1:
            status = "duplicate_raw_candidates"
        elif filing_candidates:
            status = "filing_membership_only"
        else:
            status = "unmatched_current_snapshot"
        for ordinal in candidates:
            source_to_versions[ordinal].append((row["id"], row["version"]))
        versions.append({
            "notice_id": row["id"], "version": row["version"],
            "old_key": row["dedupe_key"],
            "version_hash": _digest(row["fields_json"].encode()),
            "raw_hash": raw_hash, "filing": filing,
            "status": status, "candidate_ordinals": candidates,
            "candidate_filings": sorted({
                sources[ordinal - 1]["filing"] for ordinal in candidates
                if sources[ordinal - 1]["filing"]
            }),
            "evidence": {
                "employer_name": fields.get("employer_name"),
                "location": fields.get("location"),
                "notice_date": fields.get("notice_date"),
                "effective_date": fields.get("effective_date"),
                "employees_affected": fields.get("employees_affected"),
                "is_amendment": fields.get("is_amendment"),
                "source_notice_id": fields.get("source_notice_id"),
                "raw": raw,
            },
        })
    for item in sources:
        item["candidate_versions"] = [
            {"notice_id": notice_id, "version": version}
            for notice_id, version in sorted(source_to_versions[item["ordinal"]])
        ]
    statuses: dict[str, int] = defaultdict(int)
    for item in versions:
        statuses[item["status"]] += 1
    return {
        "state": state, "source_csv": str(source_csv),
        "source_sha256": _digest(source_bytes),
        "summary": {
            "stored_versions": len(versions), "source_rows": len(sources),
            "statuses": dict(sorted(statuses.items())),
            "source_rows_without_candidate": sum(
                not item["candidate_versions"] for item in sources
            ),
            "source_rows_multiple_notices": sum(
                len({v["notice_id"] for v in item["candidate_versions"]}) > 1
                for item in sources
            ),
            "notices_multiple_explicit_filings": sum(
                len(filings) > 1 for filings in notice_filings.values()
            ),
        },
        "versions": versions, "source_rows": sources,
    }
