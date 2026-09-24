"""Export source-backed notice relationships without inferred duplicate links.

Only source-specific migrations may create notice_links. Name similarity,
proximity, and amendment markers do not establish two source rows as one event.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path


# Retire historical heuristic links on every rebuild while preserving links
# whose source-specific migration owns their evidence and lifecycle.
RETIRED_METHODS = ("marker", "declared", "amendment", "dated-twin", "fuzzy")


def rebuild(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Remove legacy inferred relationships and report retained source links."""
    conn.execute(
        "DELETE FROM notice_links WHERE method IN (%s)"
        % ",".join("?" * len(RETIRED_METHODS)),
        RETIRED_METHODS,
    )
    rows = conn.execute(
        "SELECT kind, method, COUNT(*) AS n FROM notice_links GROUP BY kind, method"
    ).fetchall()
    if commit:
        conn.commit()
    counts = {f"{r['kind']}/{r['method']}": r['n'] for r in rows}
    return {"links": sum(counts.values()), "by_kind_method": counts}


def export_links_csv(conn: sqlite3.Connection, path: Path) -> int:
    rows = conn.execute(
        """SELECT l.kind, l.score, l.method, l.detail,
                  n.state, n.employer_name AS employer, n.notice_date,
                  b.employer_name AS related_employer, b.notice_date AS related_notice_date,
                  n.dedupe_key, b.dedupe_key AS related_dedupe_key
           FROM notice_links l
           JOIN notices n ON n.id = l.notice_id
           JOIN notices b ON b.id = l.related_id
           ORDER BY n.state, n.notice_date, n.employer_name"""
    ).fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["kind", "score", "method", "detail", "state", "employer", "notice_date",
             "related_employer", "related_notice_date", "dedupe_key", "related_dedupe_key"]
        )
        writer.writerows([tuple(r) for r in rows])
    return len(rows)
