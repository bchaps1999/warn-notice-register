"""Deep-history backfill from Big Local News's integrated dataset.

BLN's warn-github-flow pipeline has run every few hours for years and
*accumulates* notices in data/warn-transformer/processed/integrated.csv on
the `transformer` branch — including notices that state portals no longer
display (e.g. CT's new portal only shows current listings; NY's Tableau only
the current year). The rows are already in BLN's canonical schema, so they
skip our normalizer and map straight to our record shape.

Safety rule: for each state we only ingest rows strictly OLDER than the
oldest notice we already hold. Location formatting differs between BLN's
transformers and ours, so overlapping date ranges could mint near-duplicate
dedupe keys; disjoint ranges cannot.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import urllib.request
from pathlib import Path

from warnlive.normalize.engine import _clean_text, _dedupe_key, _record_hash
from warnlive.registry import Registry

INTEGRATED_URL = (
    "https://raw.githubusercontent.com/biglocalnews/warn-github-flow/"
    "transformer/data/warn-transformer/processed/integrated.csv"
)

logger = logging.getLogger("warnlive")


def download(dest_dir: Path) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "bln_integrated.csv"
    with urllib.request.urlopen(INTEGRATED_URL, timeout=300) as resp:
        dest.write_bytes(resp.read())
    return dest


def to_canonical(row: dict, source_url: str | None) -> dict:
    is_closure = {"True": True, "False": False}.get(row.get("is_closure"), None)
    is_temporary = {"True": 1, "False": 0}.get(row.get("is_temporary"), None)
    jobs = row.get("jobs") or None
    if jobs == "0":
        jobs = None
    rec = {
        "state": row["postal_code"].upper(),
        "employer_name": _clean_text(row.get("company")),
        "location": _clean_text(row.get("location")),
        "notice_date": row.get("notice_date") or None,
        "effective_date": row.get("effective_date") or None,
        "employees_affected": int(jobs) if jobs else None,
        "layoff_type": (
            "closure" if is_closure else "mass_layoff" if is_closure is False else "unknown"
        ),
        "is_temporary": is_temporary,
        "is_amendment": int(row.get("is_amendment") == "True"),
        "source_url": source_url,
        "source_notice_id": row.get("hash_id"),
        "raw_extra": json.dumps(row, sort_keys=True, ensure_ascii=False),
    }
    rec["dedupe_key"] = _dedupe_key(rec)
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def older_rows_by_state(
    csv_path: Path,
    conn: sqlite3.Connection,
    registry: Registry,
    states: list[str] | None = None,
) -> dict[str, list[dict]]:
    """Canonical records per state, restricted to rows strictly older than
    the state's current oldest notice (or all rows if we hold none)."""
    cutoffs = {
        r["state"]: r["oldest"]
        for r in conn.execute(
            "SELECT state, MIN(notice_date) AS oldest FROM notices "
            "WHERE notice_date IS NOT NULL GROUP BY state"
        )
    }
    wanted = {s.upper() for s in states} if states else None
    out: dict[str, list[dict]] = {}
    with open(csv_path) as fh:
        for row in csv.DictReader(fh):
            postal = row["postal_code"].upper()
            if wanted is not None and postal not in wanted:
                continue
            if postal.lower() not in registry:
                continue
            date = row.get("notice_date") or ""
            cutoff = cutoffs.get(postal)
            if not date or (cutoff and date >= cutoff):
                continue
            cfg = registry[postal.lower()]
            rec = to_canonical(row, cfg.source_url)
            if rec["employer_name"] is None:
                continue
            out.setdefault(postal, []).append(rec)
    return out
