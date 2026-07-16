"""Revision/duplicate detection: link notices, never merge them.

Four detectors, highest confidence first:

1. `marker`   — the employer name itself carries an amendment marker
                ("Acme Corp (Amended)", "SA Automotive (2nd notice)",
                "(Rescission)"); link to the base filing.
2. `declared` — the source flagged the row is_amendment=1; link to the most
                recent earlier filing by the same employer.
3. `amendment`— same employer files again within AMEND_WINDOW_DAYS at the
                same location: almost always a revision. Same employer,
                near dates but a *different* location is usually a separate
                site, so it's only a low-confidence possible_duplicate.
4. `fuzzy`    — same state/day/location but near-identical (not identical)
                employer spellings ("Hostess Brand Inc." / "Hostess Brands,
                Inc."). Guarded: if the digit sequences embedded in the two
                names differ ("Store 3528" vs "Store 3356"), they are
                different sites no matter how similar the strings are.

Every detector uses the non-employer evidence too: location similarity,
effective-date proximity, and headcount agreement adjust the score.
Pairs in the gray zone go to a review CSV instead of the table.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import jellyfish

from warnlive.normalize.engine import _fold

AMEND_WINDOW_DAYS = 45
FUZZY_LINK = 0.93
FUZZY_REVIEW = 0.88

MARKER = re.compile(
    r"[\s\-–—]*[\(\[]?\s*"
    r"(amend(?:ed|ment)?\s*#?\s*\d*|revis(?:ed|ion)|rescission|rescinded|"
    r"updated?|corrected?|correction|(?:2nd|3rd|second|third)\s+notice)"
    r"\s*[\)\]]?\s*$",
    re.IGNORECASE,
)


@dataclass
class Notice:
    id: int
    state: str
    employer: str
    location: str | None
    notice_date: str | None
    effective_date: str | None
    jobs: int | None

    def __post_init__(self):
        self.fold = _fold(self.employer)
        self.base = MARKER.sub("", self.employer).strip()
        self.base_fold = _fold(self.base)
        self.has_marker = bool(MARKER.search(self.employer))
        self.site_ids = _site_identifiers(self.base)
        self.loc_fold = _fold(self.location)


def _parse(d: str | None) -> date | None:
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def _loc_score(a: Notice, b: Notice) -> float:
    """Location agreement in [0,1]; 0.5 = no evidence either way."""
    if not a.loc_fold or not b.loc_fold:
        return 0.5
    if a.loc_fold == b.loc_fold:
        return 1.0
    # Sources format locations differently ("Hartford, CT" vs "Hartford"):
    # containment counts as agreement.
    if a.loc_fold in b.loc_fold or b.loc_fold in a.loc_fold:
        return 0.9
    return jellyfish.jaro_winkler_similarity(a.loc_fold, b.loc_fold)


def _jobs_score(a: Notice, b: Notice) -> float:
    if a.jobs is None or b.jobs is None:
        return 0.5
    if a.jobs == b.jobs:
        return 1.0
    return min(a.jobs, b.jobs) / max(a.jobs, b.jobs)


def _eff_score(a: Notice, b: Notice) -> float:
    da, db = _parse(a.effective_date), _parse(b.effective_date)
    if da is None or db is None:
        return 0.5
    gap = abs((da - db).days)
    return 1.0 if gap == 0 else 0.8 if gap <= 60 else 0.2


_ROMAN = re.compile(r"^[ivxl]{1,4}$")


def _site_identifiers(name: str) -> tuple:
    """Tokens that identify a specific site: anything containing a digit
    ("3528", "MAQ8", "#1901") plus standalone roman numerals ("II")."""
    ids = []
    for tok in re.split(r"[\s\-#()/,.]+", name.lower()):
        if not tok:
            continue
        if any(ch.isdigit() for ch in tok) or _ROMAN.match(tok):
            ids.append(tok)
    return tuple(ids)


def _digit_conflict(a: Notice, b: Notice) -> bool:
    """Different site identifiers = different sites, no matter how similar
    the rest of the name is (KMART Store 3528 vs 3356, Amazon MAQ8 vs MAI8,
    Holdings II vs I)."""
    return bool(a.site_ids and b.site_ids and a.site_ids != b.site_ids)


@dataclass
class Link:
    notice_id: int
    related_id: int
    kind: str
    score: float
    method: str
    detail: str


def detect(conn: sqlite3.Connection) -> tuple[list[Link], list[dict]]:
    """Return (links, review_rows). Deterministic over the notices table."""
    notices = [
        Notice(r["id"], r["state"], r["employer_name"], r["location"],
               r["notice_date"], r["effective_date"], r["employees_affected"])
        for r in conn.execute(
            "SELECT id, state, employer_name, location, notice_date, "
            "effective_date, employees_affected FROM notices "
            "WHERE employer_name IS NOT NULL"
        )
    ]
    declared = {
        r["id"] for r in conn.execute("SELECT id FROM notices WHERE is_amendment = 1")
    }

    links: list[Link] = []
    review: list[dict] = []
    seen: set[tuple[int, int, str]] = set()

    def add(n: Notice, base: Notice, kind: str, score: float, method: str, detail: str):
        key = (n.id, base.id, kind)
        if key in seen or n.id == base.id:
            return
        seen.add(key)
        links.append(Link(n.id, base.id, kind, round(score, 3), method, detail))

    # Index by (state, base employer fold)
    by_employer: dict[tuple[str, str], list[Notice]] = {}
    for n in notices:
        by_employer.setdefault((n.state, n.base_fold), []).append(n)

    for group in by_employer.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda n: (n.notice_date or "", n.id))
        for i, n in enumerate(group):
            earlier = [g for g in group[:i] if g.id != n.id]
            if not earlier:
                continue
            evidence = lambda o: 0.5 * _loc_score(n, o) + 0.3 * _eff_score(n, o) + 0.2 * _jobs_score(n, o)

            # 1. marker in the name
            if n.has_marker:
                base = max(earlier, key=lambda o: (evidence(o), o.notice_date or ""))
                add(n, base, "amendment_of", 0.95 + 0.05 * evidence(base), "marker",
                    f"name marker {n.employer!r} -> {base.employer!r}")
                continue

            # 2. source-declared amendment
            if n.id in declared:
                base = max(earlier, key=lambda o: (evidence(o), o.notice_date or ""))
                add(n, base, "amendment_of", 0.9 + 0.1 * evidence(base), "declared",
                    "source flagged is_amendment")
                continue

            # 3. refiling within the window
            dn = _parse(n.notice_date)
            for o in earlier:
                do = _parse(o.notice_date)
                if dn is None or do is None:
                    continue
                gap = (dn - do).days
                if not (0 < gap <= AMEND_WINDOW_DAYS):
                    continue
                if _digit_conflict(n, o):
                    continue
                loc = _loc_score(n, o)
                score = 0.55 + 0.3 * loc + 0.15 * _eff_score(n, o)
                if loc >= 0.85:
                    add(n, o, "amendment_of", score, "amendment",
                        f"refiled {gap}d later, same location")
                elif loc >= 0.4:
                    add(n, o, "possible_duplicate", score * 0.85, "amendment",
                        f"refiled {gap}d later, location score {loc:.2f}")

    # 4. fuzzy spelling variants: block by (state, notice_date, location fold)
    by_day_loc: dict[tuple, list[Notice]] = {}
    for n in notices:
        if n.notice_date:
            by_day_loc.setdefault((n.state, n.notice_date, n.loc_fold), []).append(n)
    for group in by_day_loc.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.fold == b.fold or _digit_conflict(a, b):
                    continue
                jw = jellyfish.jaro_winkler_similarity(a.employer.lower(), b.employer.lower())
                if jw < FUZZY_REVIEW:
                    continue
                score = jw * (0.7 + 0.3 * _jobs_score(a, b))
                later, base = (a, b) if (a.id > b.id) else (b, a)
                detail = f"jw={jw:.3f} {a.employer!r} ~ {b.employer!r}"
                if jw >= FUZZY_LINK:
                    add(later, base, "possible_duplicate", score, "fuzzy", detail)
                else:
                    review.append({
                        "state": a.state, "notice_date": a.notice_date,
                        "employer_a": a.employer, "employer_b": b.employer,
                        "location": a.location, "jw": round(jw, 3),
                        "jobs_a": a.jobs, "jobs_b": b.jobs,
                        "id_a": a.id, "id_b": b.id,
                    })
    return links, review


def rebuild(conn: sqlite3.Connection, review_path: Path | None = None) -> dict:
    """Recompute all links (idempotent; detection is deterministic)."""
    links, review = detect(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("DELETE FROM notice_links")
    conn.executemany(
        "INSERT INTO notice_links (notice_id, related_id, kind, score, method, detail, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [(l.notice_id, l.related_id, l.kind, l.score, l.method, l.detail, now) for l in links],
    )
    conn.commit()

    if review_path is not None and review:
        review_path.parent.mkdir(parents=True, exist_ok=True)
        with open(review_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(review[0].keys()))
            writer.writeheader()
            writer.writerows(sorted(review, key=lambda r: -r["jw"]))

    counts: dict = {}
    for l in links:
        counts[(l.kind, l.method)] = counts.get((l.kind, l.method), 0) + 1
    return {"links": len(links), "review": len(review),
            "by_kind_method": {f"{k}/{m}": v for (k, m), v in sorted(counts.items())}}


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
