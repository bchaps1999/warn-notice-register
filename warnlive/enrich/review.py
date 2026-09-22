"""Identity near-misses, collected for later adjudication.

Every matching rule ends in a yes or a no, and the project's standing rule
is that ambiguity matches nothing — a missing identifier costs only
enrichment, a wrong one poisons every join made against it. But a "no" is
not always a "there is nothing here": often the rule saw a plausible
registrant and refused it for want of a tiebreaker no algorithm has.

This writes those refusals out, ranked by how much they would matter
(workers affected), with the evidence that produced them: the candidate's
name and era, the gate that rejected it, and how the employer files. That
is enough for a human or a model to decide later, from the same facts the
matcher had, plus the world knowledge it lacks.

Decisions come back through data/reference/identity_overrides.csv, which
the annotator applies ahead of every automatic tier. The file records who
decided and why, so an override can be audited or revoked; nothing here
writes it, and no adjudication is ever inferred.
"""

from __future__ import annotations

import csv
import gzip
import logging
import re
from pathlib import Path

logger = logging.getLogger("warnlive")

REVIEW_PATH = Path("data/health/identity_review.csv")
OVERRIDES_PATH = Path("data/reference/identity_overrides.csv")
REVIEW_FIELDS = [
    "normalized_name",
    "employer_name",
    "states",
    "notices",
    "workers",
    "years",
    "source",
    "candidate_id",
    "candidate_name",
    "ticker",
    "rejected_by",
    "note",
]
OVERRIDE_FIELDS = [
    "normalized_name",
    "scope_state", "scope_year", "scope_location",
    "decision",
    "cik",
    "ein",
    "lei",
    "wikidata_qid",
    "decided_by",
    "decided_at",
    "note",
]


def scope_location(value: str | None) -> str:
    """Conservative location key: case/spacing only, never drop geography."""
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def scope_tuple(row: dict) -> tuple[str, str, str, str]:
    return (
        row.get("normalized_name") or "",
        (row.get("scope_state") or "").upper(),
        str(row.get("scope_year") or ""),
        scope_location(row.get("scope_location")),
    )


def load_override_rows(path: Path = OVERRIDES_PATH) -> dict[str, list[dict]]:
    """Preserve every legacy and scoped row; never collapse duplicate names."""
    out: dict[str, list[dict]] = {}
    if path.exists():
        with open(path, newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("normalized_name"):
                    out.setdefault(row["normalized_name"], []).append(row)
    return out


def select_override(rows: dict[str, list[dict]], normalized_name: str | None,
                    state: str | None, year: str | int | None,
                    location: str | None) -> dict | None:
    """Select one explicit accepted identity in exactly this notice scope.

    An unknown state, year, or location matches only a decision carrying the
    same unknown value. Legacy unscoped CIK rows and conflicting scoped rows
    grant nothing, but remain readable for audit.
    """
    key = (normalized_name or "", (state or "").upper(),
           str(year or ""), scope_location(location))
    matches = [r for r in rows.get(key[0], [])
               if scope_tuple(r) == key
               and all(f in r for f in ("scope_state", "scope_year", "scope_location"))
               and (r.get("decision") or "").strip().lower() == "accept"
               and any(r.get(f) for f in ("cik", "ein", "lei", "wikidata_qid"))]
    if not matches:
        return None
    values = {tuple(r.get(f) or "" for f in ("cik", "ein", "lei", "wikidata_qid"))
              for r in matches}
    return matches[0] if len(values) == 1 else None


def conflicting_override(rows: dict[str, list[dict]], normalized_name: str | None,
                         state: str | None, year: str | int | None,
                         location: str | None) -> bool:
    """Whether accepted rows in this exact scope disagree on identity."""
    key = (normalized_name or "", (state or "").upper(),
           str(year or ""), scope_location(location))
    values = {
        tuple(r.get(f) or "" for f in ("cik", "ein", "lei", "wikidata_qid"))
        for r in rows.get(key[0], [])
        if scope_tuple(r) == key
        and (r.get("decision") or "").strip().lower() == "accept"
        and any(r.get(f) for f in ("cik", "ein", "lei", "wikidata_qid"))
    }
    return len(values) > 1


def load_overrides(path: Path = OVERRIDES_PATH) -> dict[str, dict]:
    """Legacy review suppression, not an identity-selection API.

    Rejections record that a candidate was examined and rejected — "the
    1991 Midway Airlines is not the 1997 one" — and keep it from returning
    to the old name-wide review queue. Unscoped historical CIK rows do not
    enter this map and cannot grant an identity.

    A rejection is about the candidates that were on the table, not about
    the employer: it stops the review from asking again, and grants
    nothing, but it does not veto a future rule that finds the right
    registrant by better evidence.
    """
    if not path.exists():
        return {}
    with open(path, newline="") as fh:
        return {
            row["normalized_name"]: row
            for row in csv.DictReader(fh)
            if row.get("normalized_name")
            and (row.get("decision") or "").strip().lower() == "reject"
        }


def build(conn, out_path: Path = REVIEW_PATH, limit: int = 3000) -> int:
    """Write the review file for the highest-impact unidentified employers.

    Only employers with no identity at all are considered: an employer the
    pipeline already matched needs no second opinion, and one nobody has a
    candidate for has nothing to adjudicate.
    """
    from warnlive.enrich.annotate import Annotator
    from warnlive.enrich.edgar import REFERENCE_PATH, Matcher
    from warnlive.normalize.engine import normalized_employer

    annotator = Annotator()
    matcher = Matcher() if REFERENCE_PATH.exists() else None

    employers: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT n.employer_name AS employer_name, n.state AS state, "
        "       n.location AS location, "
        "       COALESCE(n.notice_date, n.effective_date) AS d, "
        "       COALESCE(n.employees_affected, 0) AS jobs, "
        "       (SELECT v.fields_json FROM notice_versions v "
        "        WHERE v.notice_id = n.id AND v.version = n.current_version"
        "       ) AS fields_json FROM notices n"
    ):
        norm = normalized_employer(row["employer_name"])
        if not norm:
            continue
        got = annotator.annotate(
            row["employer_name"], row["d"], row["fields_json"],
            state=row["state"], location=row["location"],
        )
        if got["cik"] or got["ein"] or got["lei"] or got["wikidata_qid"]:
            continue
        e = employers.setdefault(
            norm,
            {"employer_name": row["employer_name"], "states": set(),
             "notices": 0, "workers": 0, "years": set()},
        )
        e["states"].add(row["state"])
        e["notices"] += 1
        e["workers"] += row["jobs"]
        if row["d"]:
            e["years"].add(row["d"][:4])

    ranked = sorted(employers.items(), key=lambda kv: -kv[1]["workers"])[:limit]
    overrides = load_overrides()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=REVIEW_FIELDS)
        writer.writeheader()
        for norm, e in ranked:
            if norm in overrides:
                continue  # already decided
            year = max(e["years"]) if e["years"] else None
            found = (
                matcher.candidates(e["employer_name"], int(year) if year else None)
                if matcher
                else []
            )
            for cand in found:
                writer.writerow({
                    "normalized_name": norm,
                    "employer_name": e["employer_name"],
                    "states": "|".join(sorted(e["states"])),
                    "notices": e["notices"],
                    "workers": e["workers"],
                    "years": f"{min(e['years'])}-{max(e['years'])}" if e["years"] else "",
                    "source": "edgar",
                    "candidate_id": cand["cik"],
                    "candidate_name": cand["candidate_name"],
                    "ticker": cand["ticker"],
                    "rejected_by": cand["rejected_by"],
                    "note": cand["note"],
                })
                written += 1
    logger.info(
        "identity review: %d candidate rows for %d unidentified employers -> %s",
        written, len(ranked), out_path,
    )
    return written
