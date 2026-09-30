"""Export source-backed notice relationships without inferred duplicate links.

Only source-specific rules may create notice_links. Name similarity,
proximity, and amendment markers do not establish two source rows as one event.

``rebuild`` (run by the live ``links`` command and the offline rebuild) also
regenerates ``sibling_entry`` links (method ``source_group``): additive ties
between notices that are separate listed entries of one filing. They never
merge notices or change counts. Two sources establish them:

- notices whose ``source_details.filing_group.id`` (or scalar
  ``filing_group``) is identical within a state, set by a source projection
  that grouped the rows (``NY:dashboard-filing-group:*``,
  ``IA:agency-filing-group:*``: one filing listed site by site);
- Illinois IEBS records with distinct record IDs whose published notice
  content is identical (employer, location, dates, workers, layoff type) and
  carries at least one date or worker count. Content-free rows are not tied.

Reads and writes the notices database only; no network access.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from warnlive.normalize.engine import _fold


# Retire historical heuristic links on every rebuild while preserving links
# whose source-specific migration owns their evidence and lifecycle.
RETIRED_METHODS = ("marker", "declared", "amendment", "dated-twin", "fuzzy")
SIBLING_KIND = "sibling_entry"
SOURCE_GROUP_METHOD = "source_group"
#: States whose record IDs are opaque row IDs, so identical published content
#: under two IDs is two entries of one listing rather than two filings proven.
IDENTICAL_CONTENT_STATES = ("IL",)
_CONTENT = ("notice_date", "effective_date", "employees_affected", "layoff_type",
            "is_temporary")


def _sibling_groups(conn: sqlite3.Connection) -> list[tuple[list[str], dict]]:
    """Groups of dedupe keys that a source ties together, with the basis."""
    groups: list[tuple[list[str], dict]] = []
    by_group: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT state, dedupe_key, source_details FROM notices "
        "WHERE source_details LIKE '%\"filing_group\"%'"
    ):
        value = json.loads(row["source_details"]).get("filing_group")
        if isinstance(value, dict):
            value = value.get("id")
        if value not in (None, "", {}, []):
            token = json.dumps(value, sort_keys=True, separators=(",", ":"))
            by_group[(row["state"], token)].append(row["dedupe_key"])
    for (state, token), keys in sorted(by_group.items()):
        if len(keys) > 1:
            groups.append((sorted(keys), {"basis": "source_filing_group",
                                          "filing_group": json.loads(token)}))
    by_content: dict[tuple, list[tuple[str, str]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT state, dedupe_key, source_identity, employer_name, location, %s "
        "FROM notices WHERE state IN (%s) AND source_identity IS NOT NULL"
        % (", ".join(_CONTENT), ",".join("?" * len(IDENTICAL_CONTENT_STATES))),
        IDENTICAL_CONTENT_STATES,
    ):
        if all(row[name] is None for name in
               ("notice_date", "effective_date", "employees_affected")):
            continue
        signature = (row["state"], _fold(row["employer_name"]), _fold(row["location"]),
                     *(row[name] for name in _CONTENT))
        by_content[signature].append((row["dedupe_key"], row["source_identity"]))
    for members in by_content.values():
        if len(members) > 1 and len({identity for _, identity in members}) == len(members):
            groups.append((sorted(key for key, _ in members), {
                "basis": "identical_content_distinct_source_records",
                "source_identities": sorted(identity for _, identity in members),
            }))
    return groups


def link_source_groups(conn: sqlite3.Connection) -> int:
    """Regenerate sibling_entry links; each member points at the group's first key."""
    conn.execute("DELETE FROM notice_links WHERE method = ?", (SOURCE_GROUP_METHOD,))
    ids = {}
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    count = 0
    for keys, detail in _sibling_groups(conn):
        for key in keys:
            if key not in ids:
                ids[key] = conn.execute(
                    "SELECT id FROM notices WHERE dedupe_key = ?", (key,)).fetchone()[0]
        anchor = keys[0]
        for key in keys[1:]:
            conn.execute(
                "INSERT OR IGNORE INTO notice_links "
                "(notice_id, related_id, kind, score, method, detail, created_at) "
                "VALUES (?, ?, ?, 1.0, ?, ?, ?)",
                (ids[key], ids[anchor], SIBLING_KIND, SOURCE_GROUP_METHOD,
                 json.dumps(detail, sort_keys=True), created),
            )
            count += 1
    return count


def rebuild(conn: sqlite3.Connection, *, commit: bool = True) -> dict:
    """Remove legacy inferred relationships, regenerate source-group links,
    and report retained source links."""
    conn.execute(
        "DELETE FROM notice_links WHERE method IN (%s)"
        % ",".join("?" * len(RETIRED_METHODS)),
        RETIRED_METHODS,
    )
    link_source_groups(conn)
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
