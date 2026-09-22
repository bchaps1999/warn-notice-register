"""Read-only reconciliation of stored South Carolina rows to cached PDFs.

This intentionally does not write the database or invoke ingest.  SC's old
extractor conflated date roles, so a new dedupe key must be reviewed against
the original version evidence before a migration can safely change identity.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable

from warnlive.fetch.custom.sc import parse_pdf
from warnlive.normalize.engine import _dedupe_key

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


def _fold(value: object) -> str:
    return _NON_ALNUM.sub(" ", str(value or "").lower()).strip()


def _compact(value: object) -> str:
    """Compare PDF text with/without a line-break-derived space."""
    return _NON_ALNUM.sub("", str(value or "").lower())


def _iso(value: str | None) -> str:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value or "", fmt).date().isoformat()
        except ValueError:
            pass
    return ""


def _date_tokens(value: object) -> set[str]:
    return {_iso(token) for token in _DATE.findall(str(value or "")) if _iso(token)}


def proposed_key(row: dict[str, object]) -> str:
    """Legacy employer/date/county key for collision analysis, not an apply key.

    Corrected SC rows also carry source-row identity, which this old key
    intentionally omits.  A migration must choose and review its own target.
    """
    return _dedupe_key({
        "state": "SC", "employer_name": row.get("company"),
        "notice_date": _iso(str(row.get("notice_date") or "")) or None,
        "location": row.get("county"),
    })


def source_identity(row: dict[str, object]) -> str | None:
    """Return the same artifact/ordinal identity used by SC normalization."""
    artifact = str(row.get("source") or "").strip()
    ordinal = str(row.get("source_row") or "").strip()
    return f"SC:{artifact}:{ordinal}" if artifact and ordinal else None


def source_key(row: dict[str, object]) -> str | None:
    identity = source_identity(row)
    if not identity:
        return None
    return _dedupe_key({"state": "SC", "source_identity": identity})


def cached_rows(cache_dir: Path) -> list[dict[str, str]]:
    """Read every cached SC report, attaching its source artifact identifier."""
    rows: list[dict[str, str]] = []
    for path in sorted(Path(cache_dir).glob("*.pdf")):
        for ordinal, row in enumerate(parse_pdf(path), 1):
            rows.append({**row, "source": f"sc/{path.name}",
                         "source_row": str(ordinal)})
    return rows


@dataclass(frozen=True)
class StoredRow:
    notice_id: int
    version: int
    old_key: str
    source: str
    employer: str
    county: str
    date: str
    jobs: int | None


@dataclass(frozen=True)
class Match:
    old: StoredRow
    status: str  # exact, ambiguous, unmatched
    candidates: tuple[dict[str, str], ...] = ()

    @property
    def new_key(self) -> str | None:
        return proposed_key(self.candidates[0]) if self.status == "exact" else None

    @property
    def source_key(self) -> str | None:
        return source_key(self.candidates[0]) if self.status == "exact" else None


@dataclass
class Report:
    matches: list[Match] = field(default_factory=list)
    cached_count: int = 0

    @property
    def exact(self) -> list[Match]:
        return [match for match in self.matches if match.status == "exact"]

    @property
    def ambiguous(self) -> list[Match]:
        return [match for match in self.matches if match.status == "ambiguous"]

    @property
    def unmatched(self) -> list[Match]:
        return [match for match in self.matches if match.status == "unmatched"]

    @property
    def proposed(self) -> list[dict[str, object]]:
        """Evidence candidates only; these are not a migration manifest."""
        return [
            {"notice_id": match.old.notice_id, "version": match.old.version,
             "old_key": match.old.old_key, "new_key": match.new_key,
             "source": match.old.source,
             "source_identity": source_identity(match.candidates[0]),
             "source_key": match.source_key}
            for match in self.exact
        ]

    @property
    def source_key_collisions(self) -> dict[str, list[Match]]:
        """Distinct legacy notices mapped to one actual SC source-row key."""
        groups: dict[str, list[Match]] = defaultdict(list)
        for match in self.exact:
            if match.source_key:
                groups[match.source_key].append(match)
        return {key: group for key, group in groups.items()
                if len({match.old.notice_id for match in group}) > 1}

    @property
    def collision_groups(self) -> dict[str, list[Match]]:
        groups: dict[str, list[Match]] = defaultdict(list)
        for match in self.exact:
            groups[match.new_key or ""].append(match)
        # Several stored versions for *one* notice are history, not a key
        # collision.  A proposed key shared by distinct notices needs review.
        return {
            key: group for key, group in groups.items()
            if len({match.old.notice_id for match in group}) > 1
        }

    def summary(self) -> dict[str, int]:
        return {
            "stored_versions": len(self.matches), "cached_rows": self.cached_count,
            "exact": len(self.exact), "ambiguous": len(self.ambiguous),
            "unmatched": len(self.unmatched), "collision_groups": len(self.collision_groups),
        }


def stored_rows(conn: sqlite3.Connection) -> list[StoredRow]:
    """Return every SC version with its original raw fields, never canonical guesses."""
    result: list[StoredRow] = []
    query = """
        SELECT n.id AS notice_id, n.dedupe_key, v.version, v.fields_json
        FROM notices n JOIN notice_versions v ON v.notice_id = n.id
        WHERE n.state = 'SC' ORDER BY n.id, v.version
    """
    for row in conn.execute(query):
        try:
            fields = json.loads(row["fields_json"])
            raw = fields.get("raw_extra") or "{}"
            raw = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            raw = {}
        result.append(StoredRow(
            notice_id=row["notice_id"], version=row["version"], old_key=row["dedupe_key"],
            source=str(raw.get("source") or ""), employer=str(raw.get("company") or ""),
            county=str(raw.get("location") or ""), date=str(raw.get("date") or ""),
            jobs=_integer(raw.get("jobs")),
        ))
    return result


def _integer(value: object) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _matches(old: StoredRow, candidate: dict[str, str]) -> bool:
    """Require every stable old/new field; uncertain evidence never matches."""
    if not old.source or old.source != candidate.get("source"):
        return False
    if not old.employer or _compact(old.employer) != _compact(candidate.get("company")):
        return False
    old_county, new_county = _compact(old.county), _compact(candidate.get("county"))
    # Some cached table cells cut "Multiple Counties" into the next column.
    # A long prefix remains conservative alongside source, employer, date and
    # exact workers, while recovering that known PDF rendering defect.
    if not old_county or not new_county or not (
        old_county == new_county
        or (len(old_county) >= 10 and new_county.startswith(old_county))
    ):
        return False
    workers = _integer(candidate.get("impacted"))
    if old.jobs is None or workers is None or old.jobs != workers:
        return False
    old_dates = _date_tokens(old.date)
    candidate_dates = _date_tokens(candidate.get("effective_date")) | _date_tokens(
        candidate.get("effective_end_date")
    ) | _date_tokens(candidate.get("legacy_date"))
    if old_dates and candidate_dates:
        return bool(old_dates & candidate_dates)
    # Preserve an unparseable legacy date only when its exact source text
    # survived on both sides.  This still has four independent identifiers.
    return bool(
        old.date and old.date.strip() == str(candidate.get("legacy_date") or "").strip()
    )


def reconcile(
    conn: sqlite3.Connection, rows: Iterable[dict[str, str]],
) -> Report:
    """Produce a conservative report; this function performs no DB writes."""
    cached = list(rows)
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in cached:
        by_source[row.get("source", "")].append(row)
    matches: list[Match] = []
    for old in stored_rows(conn):
        candidates = tuple(row for row in by_source.get(old.source, []) if _matches(old, row))
        status = "exact" if len(candidates) == 1 else "ambiguous" if candidates else "unmatched"
        matches.append(Match(old=old, status=status, candidates=candidates))
    return Report(matches=matches, cached_count=len(cached))
