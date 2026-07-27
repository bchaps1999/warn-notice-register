"""Deciding what a filed location string actually names.

The resolver places the large majority of what states file, and refuses the
rest for a reason recorded next to it. What is left is not a parsing problem:
Kansas files against workforce investment areas, Illinois files an airport,
Ohio files "Various Cities/Various Counties", and no rule over the string will
ever turn those into a county. Some of them name a place a person would
recognise and a regex will not; the others name no place at all, and saying so
is the answer.

A proposal is written as an alias and the resolver is run again on the
original string; either it now resolves to a Census place or county, or the
proposal is worth nothing. An invented city fails because it is not in the
gazetteer, so for a city the model can widen what the resolver reaches
without being able to make it accept something that does not exist.

That gate is total for a city and empty for a county, and the difference
matters more than it looks. Re-running the resolver on a county answer
confirms only that the county exists, and every real county does. Nor is
this a gap that better evidence would close: the queue is made of exactly
the strings the resolver could not place, so a county answer is always about
a name the gazetteer does not know in that state. There is nothing to check
it against, ever.

New Jersey filed "Salisbury". The model answered Middlesex County at
confidence 0.99, calling it an unincorporated community, and the gate passed
it because Middlesex County is real. There is no Salisbury anywhere in New
Jersey — the employer was a Delhaize banner, and Delhaize America is in
Salisbury, North Carolina. So county answers are staged for a person rather
than written, and confidence is not allowed to stand in for evidence: 0.99
was the score on a place that does not exist.
"""

from __future__ import annotations

import csv
import logging
from datetime import date
from pathlib import Path

from warnlive.adjudicate.queue import (
    ABSTAINED,
    ACCEPTED,
    REJECTED,
    STAGED,
    Adjudicator,
    Decision,
)
from warnlive.enrich.places import ALIAS_PATH, REVIEW_PATH, Resolver, fold

logger = logging.getLogger("warnlive")

STAGING_PATH = Path("data/health/places_adjudicated.csv")
ALIAS_FIELDS = ["state", "filed_name", "decision", "census_name", "kind", "note"]
STAGING_FIELDS = [
    "state", "location", "notices", "workers", "reason",
    "kind", "census_name", "resolved_to", "confidence", "elsewhere",
    "outcome", "gate", "note",
]

# A location naming two sites is not a failure of this queue; it is a field
# that can hold one answer being asked to hold two. Recorded, never written.
MULTIPLE = "multiple"
UNRESOLVABLE = "unresolvable"
PLACE_KINDS = {"place", "county"}

SYSTEM = """\
You are resolving location strings from US WARN layoff notices to Census \
places. Each row is one string exactly as a state agency filed it, together \
with the state it was filed in and why an automatic resolver refused it.

For each row decide what the string names:

- "place": it names an incorporated city, town, village or township. Give the \
city's name in "census_name".
- "county": it names a neighbourhood, unincorporated community, airport, \
military base or similar that sits in no city, or it names a county outright. \
Give the county's name in "census_name", including the word County.
- "multiple": it names two or more distinct sites.
- "unresolvable": it names no geography at all. Workforce investment areas, \
service delivery regions, "Various Cities", "Statewide" and similar are \
unresolvable — they are administrative areas, not places.
- "unknown": you cannot tell.

Rules. The place must be in the state given; a string naming somewhere in \
another state is "unknown", not a guess. Never invent a name — if you are not \
confident the place exists under that name, answer "unknown". Prefer "county" \
over "place" whenever the site is not inside an incorporated municipality. \
Give "confidence" between 0 and 1.

Reply with JSON only, in exactly this form:

{"results": [
  {"id": 1, "kind": "place", "census_name": "Chicago", "confidence": 0.97,
   "note": "O'Hare airport is within Chicago city limits"},
  {"id": 2, "kind": "unresolvable", "census_name": "", "confidence": 0.99,
   "note": "a Kansas workforce investment area, not a place"}
]}"""


def load_queue(path: Path = REVIEW_PATH, min_workers: int = 0) -> list[dict]:
    """The unresolved locations, worst first — the review file's own order."""
    if not path.exists():
        return []
    with open(path, newline="") as fh:
        rows = [
            {
                "state": (r.get("state") or "").upper(),
                "location": r.get("location") or "",
                "notices": int(r.get("notices") or 0),
                "workers": int(r.get("workers") or 0),
                "reason": r.get("reason") or "",
            }
            for r in csv.DictReader(fh)
        ]
    rows = [r for r in rows if r["state"] and r["location"]]
    rows = [r for r in rows if r["workers"] >= min_workers]
    return sorted(rows, key=lambda r: (-r["workers"], r["state"], r["location"]))


class Places(Adjudicator):
    """Proposes an alias; the resolver decides whether it was worth anything."""

    task = "places"
    prompt_version = "places-v1"
    required = {"kind": str, "census_name": str, "confidence": (int, float)}
    batch_size = 12
    max_tokens_per_row = 120

    def __init__(self, threshold: float = 0.8, resolver: Resolver | None = None,
                 auto_county: bool = False) -> None:
        self.threshold = threshold
        # Whether a county-level answer may be written without a person.
        #
        # Off, because nothing can check one. The gate works by writing the
        # proposal as an alias and asking the resolver again — which for a
        # county answer only confirms the county exists, and every real
        # county does. Worse, this queue holds exactly the strings the
        # resolver could not place, so a county answer is always about a name
        # the gazetteer does not know in that state: there is no evidence to
        # be had, not merely none found.
        #
        # New Jersey filed "Salisbury" and the model returned Middlesex
        # County at confidence 0.99, calling it an unincorporated community.
        # There is no Salisbury anywhere in New Jersey; the employer was a
        # Delhaize banner and Delhaize America is in Salisbury, North
        # Carolina. Confidence said nothing — 0.99 for a place that does not
        # exist — so there is no threshold that separates these, and the
        # honest place for them is the review file.
        self.auto_county = auto_county
        # One resolver for the whole run, with aliases mutated in place to try
        # a proposal. Its cache is cleared per trial, since the answer for a
        # string changes precisely when an alias for it is added.
        self.resolver = resolver if resolver is not None else Resolver()
        self._by_key: dict[str, set[str]] | None = None

    def system(self) -> str:
        return SYSTEM

    def key(self, item: dict) -> str:
        return f"{item['state']}|{item['location']}"

    def render(self, item: dict) -> dict:
        return {
            "state": item["state"],
            "location": item["location"],
            "refused_because": item["reason"],
        }

    def attested_elsewhere(self, state: str, location: str) -> list[str]:
        """States where this filed name is a real place, if not this one."""
        if self._by_key is None:
            index: dict[str, set[str]] = {}
            for st, key in list(self.resolver.places) + list(self.resolver.subdivisions):
                index.setdefault(key, set()).add(st)
            self._by_key = index
        for key, _kind in self.resolver._keys(state, location):
            states = self._by_key.get(key) or set()
            if states and state not in states:
                return sorted(states)
        return []

    def resolves(self, state: str, location: str, census_name: str, kind: str) -> dict:
        """Would this alias make the resolver place this string? Try it.

        The trial is the verification. Nothing here inspects the proposed
        name for plausibility — it is written into the alias table and the
        real resolver is asked again, so a place absent from the Census
        gazetteer fails exactly as it would in production.
        """
        alias_key = (state, fold(location))
        previous = self.resolver.aliases.get(alias_key)
        self.resolver.aliases[alias_key] = (fold(census_name), kind)
        self.resolver._cache.pop((state, location), None)
        try:
            return self.resolver.resolve(state, location)
        finally:
            if previous is None:
                self.resolver.aliases.pop(alias_key, None)
            else:
                self.resolver.aliases[alias_key] = previous
            self.resolver._cache.pop((state, location), None)

    def decide(self, item: dict, answer: dict) -> Decision:
        kind = str(answer.get("kind") or "").strip().lower()
        census_name = str(answer.get("census_name") or "").strip()
        try:
            confidence = float(answer.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        note = str(answer.get("note") or "").strip()[:300]
        state, location = item["state"], item["location"]

        base = {
            **item,
            "kind": kind,
            "census_name": census_name,
            "confidence": round(confidence, 3),
            "note": note,
        }

        if kind == UNRESOLVABLE and confidence >= self.threshold:
            return Decision(
                REJECTED,
                note=note or "names no geography",
                row={
                    **base,
                    "alias": {
                        "state": state, "filed_name": location,
                        "decision": "reject", "census_name": "", "kind": "",
                        "note": note or "names no geography",
                    },
                },
            )

        if kind == MULTIPLE:
            # Left in the ledger and the staging file only: one location
            # column cannot hold two sites, and picking one would be a
            # silent loss rather than a resolution.
            return Decision(STAGED, note="names more than one site", row={**base, "gate": "multiple"})

        if kind not in PLACE_KINDS or not census_name:
            return Decision(ABSTAINED, note=note or "no place proposed", row=None)

        got = self.resolves(state, location, census_name, kind)
        if not got.get("geo_basis"):
            # The proposal named something the gazetteer does not have in
            # this state. This is the hallucination gate, and it is the
            # resolver's own refusal rather than a check written here.
            return Decision(
                STAGED,
                note=f"{census_name!r} does not resolve in {state}",
                row={**base, "gate": "did not resolve"},
            )

        placed = got.get("place_name") or got.get("county_name")
        if confidence < self.threshold:
            return Decision(
                STAGED,
                note=f"resolves to {placed} but confidence {confidence:.2f}",
                row={**base, "gate": f"below threshold {self.threshold}"},
            )

        if kind == "county" and not self.auto_county:
            # Ranked for whoever reviews it: a filed name that is a real
            # place in other states and in none of this one is likelier to be
            # an out-of-state address than a local community. Too blunt to
            # gate on — most unincorporated communities share a name with
            # some incorporated place elsewhere — but worth showing.
            others = self.attested_elsewhere(state, location)
            note = f"county-level, unverifiable; resolves to {placed}"
            if others:
                note += f"; also a place in {','.join(others[:6])} but not {state}"
            return Decision(
                STAGED, note=note,
                row={**base, "resolved_to": placed, "gate": "county-level",
                     "elsewhere": ",".join(others)},
            )

        return Decision(
            ACCEPTED,
            note=f"resolves to {placed}",
            row={
                **base,
                "resolved_to": placed,
                "alias": {
                    "state": state,
                    "filed_name": location,
                    "decision": "",
                    "census_name": census_name,
                    "kind": kind,
                    "note": f"{note} [{placed}]".strip() if note else placed,
                },
            },
        )


def write(rows: list[dict], alias_path: Path = ALIAS_PATH,
          staging_path: Path = STAGING_PATH, decided_by: str = "") -> tuple[int, int]:
    """Append accepted aliases and rejections; stage everything unproven.

    Only rows carrying an "alias" are written to the reference file, and each
    one has already been shown to resolve — or to be a rejection, which
    grants no geography and only stops the asking. Nothing is overwritten:
    a string already decided keeps the decision it has, because a later run
    disagreeing with an earlier one is a thing to look at, not to apply.
    """
    existing: set[tuple[str, str]] = set()
    if alias_path.exists():
        with open(alias_path, newline="") as fh:
            existing = {
                ((r.get("state") or "").upper(), fold(r.get("filed_name") or ""))
                for r in csv.DictReader(fh)
            }

    aliases = []
    for row in rows:
        alias = row.get("alias")
        if not alias:
            continue
        key = (alias["state"].upper(), fold(alias["filed_name"]))
        if key in existing:
            logger.debug("places: %s already decided, leaving it alone", key)
            continue
        existing.add(key)
        if decided_by:
            stamp = f"{decided_by} {date.today().isoformat()}"
            alias["note"] = f"{alias['note']} ({stamp})" if alias["note"] else stamp
        aliases.append(alias)

    if aliases:
        write_header = not alias_path.exists()
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        with open(alias_path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=ALIAS_FIELDS)
            if write_header:
                writer.writeheader()
            for alias in aliases:
                writer.writerow({k: alias.get(k, "") for k in ALIAS_FIELDS})

    staged = [r for r in rows if not r.get("alias")]
    if staged:
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        with open(staging_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=STAGING_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(staged, key=lambda r: -int(r.get("workers") or 0)):
                writer.writerow({
                    **row,
                    "outcome": row.get("_outcome", ""),
                    "gate": row.get("gate", ""),
                })

    return len(aliases), len(staged)
