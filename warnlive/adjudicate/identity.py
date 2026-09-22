"""Deciding who an employer is, when the matcher could not.

Two-thirds of the workers in this dataset belong to employers with no
identifier at all, and the reason is rarely a dirty name. Sorted by workers,
the largest unmatched employers are Mervyn's, First Transit, ABM Aviation,
DHL Supply Chain, Fleming, Crothall Healthcare — clean names, every one. They
divide into companies that are somebody's subsidiary and file under a name
that appears in no registration, and companies that are private or defunct
and have no registrant to find. Neither is a string problem, and no amount of
normalizing reaches either.

So the model is not asked to clean a name. It is asked who the employer is,
and the answer is typed by what kind of thing it found: a registrant of its
own, somebody's subsidiary, or nobody the SEC has heard of. Each type has a
different destination, and that distinction is the point:

- a registrant becomes an identity override, claiming the employer *is* that
  filer;
- a subsidiary is linked only when SEC Exhibit 21 proves the ownership in
  the notice era. Exact SEC names are already in the generated index;
  shortened aliases are staged for review rather than promoted from a model
  claim. First Transit is not FirstGroup, and writing FirstGroup's CIK into
  its identity would conflate them in every join made afterwards;
- everything else is recorded in the ledger, which stops the same employer
  being re-asked every run, and staged for a person. It does not become an
  override: "the model said private" is the one claim in this file nothing
  can verify, and writing it would make the least-checked answer the most
  permanent. Reject rows in identity_overrides.csv are for people.

The model is never asked to decide *whether* to search. Every row is asked
for the names the employer might be registered under and for its possible
parent, and what kind of thing it found ("stance") rides along as context
for a reviewer. An earlier design routed on the stance — proposals were
only honoured when the model first said "public" — which put an
unverifiable classification in front of every gate: a wrong "private"
silently prevented the matcher from ever seeing names it would have
matched.

Nothing here trusts the model's word. A proposed name is fed to the ordinary
EDGAR matcher and must clear the same era, token-compatibility and uniqueness
gates that refused in the first place; then it must be corroborated at least
twice over by evidence the proposal never saw. A guess that clears both is
worth having. A guess that clears neither costs nothing, which is the whole
reason for proposing rather than rewriting.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import tempfile
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
from warnlive.enrich import places, subsidiaries
from warnlive.enrich.annotate import Annotator
from warnlive.enrich.edgar import REFERENCE_PATH, Matcher
from warnlive.enrich.industry import industry_from_fields_json
from warnlive.enrich import review
from warnlive.enrich.review import OVERRIDES_PATH, REVIEW_PATH
from warnlive.normalize.engine import base_employer, normalized_employer

logger = logging.getLogger("warnlive")

STAGING_PATH = Path("data/health/identity_adjudicated.csv")
SUBSIDIARY_OVERRIDES = subsidiaries.OVERRIDES_PATH

OVERRIDE_FIELDS = [
    "normalized_name", "scope_state", "scope_year", "scope_location",
    "decision", "cik", "ein", "lei", "wikidata_qid",
    "decided_by", "decided_at", "note",
]
SUBSIDIARY_FIELDS = [
    "normalized_name", "scope_state", "scope_year", "scope_location",
    "decision", "parent_cik", "parent_name", "source_year",
    "decided_by", "decided_at", "note",
]
STAGING_FIELDS = [
    "normalized_name", "scope_state", "scope_year", "scope_location",
    "employer_name", "states", "notices", "workers", "years",
    "stance", "proposed", "matched_cik", "matched_name", "cik_match", "parent_name", "parent_cik",
    "confidence", "corroborated_by", "outcome", "gate", "note",
]

# Stances that assert nothing is there to find. Recorded in the ledger (so
# the employer stops being re-asked) and staged; they grant nothing and
# write nothing — a permanent reject override takes a person.
EMPTY_STANCES = {"private", "government", "franchise", "nonexistent"}
MIN_CORROBORATORS = 2

SYSTEM = """\
You are identifying the employers named on US WARN layoff notices. Each row \
is one employer exactly as states filed it, with where and when it filed and \
how many workers its notices covered. Some rows carry candidate SEC \
registrants an automatic matcher found and then refused, with the reason.

For each row, say who the employer is:

- "proposed": names under which this employer might be registered with the \
SEC, most likely first, at most three — its full legal name, a former name, \
the name of the entity that files for it, and the plain company name itself \
if that is what you would search for. Leave it empty only when you are \
confident the employer has never had an SEC registration of its own.
- "parent_name" and "parent_cik": the corporate parent, when the employer is \
owned by another company. Give the CIK only if you are sure of it; otherwise \
leave it 0. A row may carry both proposals and a parent; give whichever you \
have evidence for.
- "stance": your reading of what kind of employer this is — "public" (files \
with the SEC itself), "subsidiary", "private", "government", "franchise" (an \
independently owned outlet of a chain), or "unknown". This is context for a \
reviewer; the proposals and the parent are what get checked.
- "confidence": 0 to 1.
- "note": the evidence, in one short sentence.

Most WARN employers are private companies, franchises, school districts, \
hospitals and staffing firms with no SEC registration at all. An empty \
"proposed" with stance "private" or "government" is a real answer and is \
expected to be common. Answer "unknown" rather than guessing. Never invent \
a CIK.

Names carry the site as well as the company — "Ford Motor Co. - Flat Rock", \
"KMART - STORE #3671". Identify the company, and ignore the site. Where a \
row has a "company" field, the site has already been cut off for you; use \
it, and go back to the filed name only if the cut looks wrong.

An automatic search of the SEC's register did not identify this employer. \
That search is literal and often fails on a company that plainly is \
registered — because the filed name carries a site, or a punctuation \
variant, or the search could not choose between two filers of the same \
name. So propose whatever names this company might be registered under, \
most likely first: its full legal name, a former name, the name of the \
entity that files for it, and the plain company name itself if that is \
what you would search for.

Where a row lists "sites", those are the towns the employer laid off in, \
largest first. Use them to tell apart companies that share a name, and to \
recognise a local employer that is not the national company of the same name.

Reply with JSON only, in exactly this form:

{"results": [
  {"id": 1, "stance": "public", "proposed": ["Mervyn's Holdings LLC"],
   "parent_name": "", "parent_cik": 0, "confidence": 0.72,
   "note": "California department store chain, filed with the SEC before 2008"},
  {"id": 2, "stance": "subsidiary", "proposed": [],
   "parent_name": "Compass Group PLC", "parent_cik": 0, "confidence": 0.93,
   "note": "Crothall Healthcare is Compass Group's healthcare support division"},
  {"id": 3, "stance": "government", "proposed": [], "parent_name": "",
   "parent_cik": 0, "confidence": 0.96, "note": "a county school district"}
]}"""


def load_queue(conn, min_workers: int = 0, limit: int | None = None) -> list[dict]:
    """Unidentified employers, worst first, with any candidates already found.

    Wider than data/health/identity_review.csv, which holds only the 418
    employers where the matcher found a candidate and then refused it. The
    other forty thousand never had a candidate at all, and among them is most
    of the missing worker-mass, so they are the queue — the review file's
    candidates are attached as evidence where they exist.
    """
    annotator = Annotator()
    resolver = places.Resolver() if places.PATH.exists() else None
    employers: dict[tuple[str, str, str, str], dict] = {}
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
        scope_state = (row["state"] or "").upper()
        scope_year = row["d"][:4] if row["d"] else ""
        scope_location = review.scope_location(row["location"])
        scope = (norm, scope_state, scope_year, scope_location)
        entry = employers.setdefault(scope, {
            "normalized_name": norm,
            "scope_state": scope_state,
            "scope_year": scope_year,
            "scope_location": scope_location,
            "filed_location": row["location"] or "",
            "employer_name": row["employer_name"],
            "cleaned_name": base_employer(row["employer_name"]),
            "states": set(), "years": set(), "notices": 0, "workers": 0,
            "source_naics": set(), "candidates": [], "sites": {},
        })
        entry["notices"] += 1
        entry["workers"] += row["jobs"] or 0
        if row["state"]:
            entry["states"].add(row["state"])
        if row["d"]:
            entry["years"].add(row["d"][:4])
        # Where the employer actually operated, resolved rather than filed.
        #
        # "Cardinal Logistics" is a hundred companies; "Cardinal Logistics,
        # Concord NC" is one, and the town is often the whole difference
        # between a name that could be anybody and a name somebody can look
        # up. Only resolved places are shown, so a string the gazetteer
        # refused never reaches the prompt as though it were a fact.
        if resolver is not None and row["state"]:
            got = resolver.resolve(
                row["state"], row["location"], row["fields_json"],
                row["employer_name"],
            )
            where = got["place_name"] or got["county_name"]
            if where:
                site = f"{where}, {row['state']}"
                entry["sites"][site] = entry["sites"].get(site, 0) + (row["jobs"] or 0)
        # The employer's own reported industry, kept aside as a corroborator:
        # it is evidence the model is never shown, so agreement with a
        # candidate's SEC industry is independent of anything it said.
        _, naics, _ = industry_from_fields_json(row["fields_json"])
        if naics:
            entry["source_naics"].add(naics)

    review_candidates = _review_candidates()
    for scope, entry in employers.items():
        entry["candidates"] = review_candidates.get(scope[0], [])

    rows = [
        {
            **e,
            "states": sorted(e["states"]),
            "years": sorted(e["years"]),
            "source_naics": sorted(e["source_naics"]),
            # Biggest sites first: a prompt has room for a few, and the ones
            # worth spending it on are where the workers were.
            "sites": [
                s for s, _ in sorted(
                    e["sites"].items(), key=lambda kv: (-kv[1], kv[0])
                )
            ],
        }
        for e in employers.values()
        if e["workers"] >= min_workers
    ]
    rows.sort(key=lambda r: (-r["workers"], r["normalized_name"]))
    return rows[:limit] if limit else rows


def _review_candidates(path: Path = REVIEW_PATH) -> dict[str, list[dict]]:
    """The candidates the matcher found and refused, by employer."""
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            name = r.get("normalized_name")
            if not name:
                continue
            out.setdefault(name, []).append({
                "name": r.get("candidate_name") or "",
                "cik": r.get("candidate_id") or "",
                "rejected_by": r.get("rejected_by") or "",
            })
    return {k: v[:4] for k, v in out.items()}


class Identity(Adjudicator):
    """Proposes a registrant or a parent; evidence decides whether to keep it."""

    task = "identity"
    #: v2 showed the cleaned company name and said it had already been
    #: searched, turning "who is this employer" into "what else might this
    #: company be registered as". That bought more parent links and more
    #: rejections and cost every direct identity: "public" fell from 25 in
    #: 200 to 12, taking Fleming, Sykes and JP Morgan Chase with it, because
    #: for those the obvious name was the right one and the search had
    #: failed for an incidental reason. v3 says the search is literal and
    #: often wrong, and asks for the plain name too. v4 stops routing on the
    #: stance: proposals are asked for on every row and always tried, so a
    #: wrong "private" can no longer keep the matcher from names it would
    #: have matched — the v2 losses were exactly that failure, one level up.
    prompt_version = "identity-v5-scoped"
    required = {"stance": str, "confidence": (int, float)}
    batch_size = 8
    max_tokens_per_row = 200
    reasoning_tokens_per_row = 420

    def __init__(self, threshold: float = 0.8, min_corroborators: int = MIN_CORROBORATORS):
        self.threshold = threshold
        self.min_corroborators = min_corroborators
        self.matcher = Matcher() if REFERENCE_PATH.exists() else None
        self.annotator = Annotator()
        self.subsidiaries = subsidiaries.Index()
        self.overrides = review.load_override_rows()
        self._spans: dict[int, tuple[int, int]] | None = None

    def system(self) -> str:
        return SYSTEM

    def key(self, item: dict) -> str:
        return json.dumps(review.scope_tuple(item), separators=(",", ":"))

    def render(self, item: dict) -> dict:
        out = {
            "employer": item["employer_name"],
            # The company part, already cut deterministically. Shown because
            # the model should be spending its attention on which registrant
            # this is, not on re-deriving that "- Flat Rock" is a place; and
            # because a cut that went wrong is visible here rather than
            # silently narrowing what gets proposed.
            **({"company": item["cleaned_name"]}
               if item.get("cleaned_name") else {}),
            "states": ",".join(item["states"][:8]),
            "years": f"{item['years'][0]}-{item['years'][-1]}" if item["years"] else "",
            "notices": item["notices"],
            "workers": item["workers"],
        }
        if item.get("filed_location"):
            out["filed_location"] = item["filed_location"]
        if item.get("sites"):
            out["sites"] = item["sites"][:4]
        if item.get("candidates"):
            out["refused_candidates"] = [
                f"{c['name']} (CIK {c['cik']}, {c['rejected_by']})"
                for c in item["candidates"]
            ]
        return out

    # -- gates ------------------------------------------------------------

    def _matches(self, item: dict, proposed: list[str]) -> dict[int, tuple[str, str]]:
        """Resolve every proposal in every notice year, retaining conflicts.

        The matcher is not relaxed for a proposal. A name the model supplies
        has to survive the same era, token-compatibility and uniqueness rules
        that refused the filed name, so a wrong guess dies here rather than
        arriving as a confident match.
        """
        if self.matcher is None:
            return {}
        years = [int(y) for y in item.get("years", []) if str(y).isdigit()] or [None]
        hits: dict[int, tuple[str, str]] = {}
        for name in proposed:
            name = (name or "").strip()
            if not name:
                continue
            for year in years:
                hit = self.matcher.match(name, year)
                if hit:
                    hits.setdefault(hit[0], (hit[2], name))
        return hits

    def _owner(self, item: dict) -> dict | None:
        """Whose Exhibit 21 lists this employer, under either of its names."""
        owners = [
            owner for name in _names(item["employer_name"])
            if (owner := self.subsidiaries.sec_parent(name))
        ]
        if len({owner["parent_cik"] for owner in owners}) > 1:
            return None
        return owners[0] if owners else None

    def _filing_span(self, cik: int) -> tuple[int, int] | None:
        """The years a registrant filed under any name, from the roster."""
        if self.matcher is None:
            return None
        if self._spans is None:
            spans: dict[int, tuple[int, int]] = {}
            for entries in self.matcher.by_name.values():
                for entry_cik, first, last, _ticker in entries:
                    got = spans.get(entry_cik)
                    spans[entry_cik] = (
                        (min(got[0], first), max(got[1], last)) if got
                        else (first, last)
                    )
            self._spans = spans
        return self._spans.get(cik)

    def _listed_exactly(self, item: dict) -> dict | None:
        """Whose Exhibit 21 lists this employer under exactly this name.

        The index deliberately tolerates a notice's shorter name, matching
        "Cessna" to CESSNA AIRCRAFT COMPANY. That tolerance is right for
        finding a parent and wrong for contradicting an identity: Boeing's
        own schedule lists Boeing Aerospace Operations, and reading that as
        "The Boeing Company is somebody's subsidiary" would refuse the one
        registrant it most obviously is. Only an exact listing contradicts.
        """
        for name in _names(item["employer_name"]):
            hit = self.subsidiaries.sec_by_name.get(name)
            if hit:
                return hit
        return None

    def _corroborate(self, item: dict, cik: int, cik_match: str,
                     parent_cik: int | None) -> list[tuple[str, bool]]:
        """Evidence for a matched CIK that the proposal itself did not supply.

        Each entry is (what the witness says, whether it is anchored to the
        CIK). The distinction matters because the witnesses are not equal:
        Wikidata naming this CIK speaks about the specific registrant
        matched. The filing calendar and a 2-digit
        sector agreement speak only about *a* registrant of this name and
        rough kind — and a same-industry namesake, the likeliest way a
        wrong match arises, passes both of those for free. So acceptance
        requires at least one anchored witness among the corroborators;
        two weak ones can only stage a row for confirmation.
        """
        found: list[tuple[str, bool]] = []

        # The registrant was filing across the whole span of these notices.
        #
        # Read from the reference file rather than from the matcher's reason
        # for matching. The reason only says the match was not *reached for*
        # across the era boundary, which nearly every method satisfies — using
        # it would hand out a free witness and make "two corroborators" mean
        # one. Covering an employer's whole filing span is a real question
        # about a real calendar, and plenty of candidates fail it.
        span = self._filing_span(cik)
        years = [int(y) for y in item.get("years", []) if y.isdigit()]
        if span and years and span[0] <= min(years) and max(years) <= span[1]:
            # Weak: any long-lived registrant covers any span. A namesake
            # that happens to be old passes this for free.
            found.append(
                (f"registrant filed {span[0]}-{span[1]}, covering the notices",
                 False)
            )

        # Wikidata, which keys its own entities by CIK, calls this registrant
        # by the name the state filed.
        #
        # A third party agreeing on the name is independent of the EDGAR name
        # match that produced the candidate: it is a different editor working
        # from different sources, and it is the only witness available to a
        # plain public company — Exhibit 21 cannot speak for one, since a
        # company does not appear in its own subsidiary schedule.
        entity = self.annotator.wikidata_by_cik.get(cik)
        if entity and entity.get("label"):
            label = normalized_employer(entity["label"])
            if label and label in set(_names(item["employer_name"])):
                found.append(
                    (f"Wikidata knows CIK {cik} as {entity['label']!r}", True)
                )

        # Exhibit 21 proves ownership, not independent CIK identity. Never
        # use it as positive identity corroboration, even if the model named
        # that same parent.

        # The industry the states published for this employer agrees with the
        # industry the SEC assigned the registrant. Weak: twenty sectors,
        # and WARN filers cluster in a handful — two same-industry namesakes
        # agree here by construction.
        sic = self.annotator.sic_by_cik.get(cik, ("", ""))[0]
        sec_naics = self.annotator.naics_by_sic.get(sic)
        if sec_naics and item.get("source_naics"):
            if any(_same_sector(sec_naics, n) for n in item["source_naics"]):
                found.append(
                    ("SEC industry agrees with the state-reported one", False)
                )

        # The IRS/GLEIF name-in-state check that used to sit here was no
        # witness at all: it looked the *employer's name* up in rosters that
        # know nothing of the matched CIK, so it attested only that some
        # entity by this name exists in a filing state — which is equally
        # consistent with the match being wrong. A nonprofit namesake
        # in-state argues against the SEC identity, and it was counting for
        # it.

        return found

    def decide(self, item: dict, answer: dict) -> Decision:
        stance = str(answer.get("stance") or "").strip().lower()
        proposed = answer.get("proposed") or []
        if isinstance(proposed, str):
            proposed = [proposed]
        proposed = [str(p) for p in proposed][:3]
        parent_name = str(answer.get("parent_name") or "").strip()
        try:
            parent_cik = int(answer.get("parent_cik") or 0) or None
        except (TypeError, ValueError):
            parent_cik = None
        try:
            confidence = float(answer.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        note = str(answer.get("note") or "").strip()[:300]

        base = {
            "normalized_name": item["normalized_name"],
            "scope_state": item.get("scope_state") or "",
            "scope_year": item.get("scope_year") or "",
            "scope_location": review.scope_location(item.get("scope_location")),
            "employer_name": item["employer_name"],
            "states": "|".join(item["states"]),
            "notices": item["notices"],
            "workers": item["workers"],
            "years": f"{item['years'][0]}-{item['years'][-1]}" if item["years"] else "",
            "stance": stance,
            "proposed": "|".join(proposed),
            "parent_name": parent_name,
            "parent_cik": parent_cik or "",
            "confidence": round(confidence, 3),
            "note": note,
        }

        # An employer somebody already adjudicated is never re-decided here.
        if review.select_override(
            self.overrides, item["normalized_name"],
            item.get("scope_state"), item.get("scope_year"),
            item.get("scope_location"),
        ):
            return Decision(STAGED, note="already decided by hand",
                            row={**base, "gate": "existing override"})

        # Proposals are tried whenever they were given, whatever the stance
        # said. The stance is one model output no gate can check, and routing
        # on it put that output in front of every gate: a wrong "private"
        # silently kept the matcher from names it would have matched.
        matcher_refused = ""
        if proposed:
            matches = self._matches(item, proposed)
            if len(matches) > 1:
                return Decision(
                    STAGED,
                    note=f"proposals resolve to different CIKs: {sorted(matches)}",
                    row={**base, "gate": "conflicting proposed CIKs"},
                )
            if not matches:
                # Not returned yet: a parent claim on the same row still
                # deserves its chance below.
                matcher_refused = (
                    f"no proposed name cleared the matcher: {', '.join(proposed)}"
                )
            else:
                cik, (cik_match, used) = next(iter(matches.items()))
                # Exhibit 21 of the matched registrant listing this employer
                # contradicts the claim being made: a company does not appear
                # in its own subsidiary schedule. The right fix is a parent
                # link rather than an identity, so a person decides.
                owner = self._listed_exactly(item)
                if owner and int(owner["parent_cik"]) == cik:
                    return Decision(
                        STAGED,
                        note=f"Exhibit 21 of {owner['parent_name']} lists it as a "
                             "subsidiary, not as the registrant",
                        row={**base, "matched_cik": cik, "matched_name": used,
                             "cik_match": cik_match,
                             "gate": "listed as its own subsidiary"},
                    )
                corroborators = self._corroborate(item, cik, cik_match, parent_cik)
                witnesses = [text for text, _anchored in corroborators]
                anchored = [text for text, anchored in corroborators if anchored]
                row = {
                    **base, "matched_cik": cik, "matched_name": used,
                    "cik_match": cik_match,
                    "corroborated_by": "; ".join(witnesses),
                }
                if confidence < self.threshold:
                    return Decision(STAGED, note=f"confidence {confidence:.2f}",
                                    row={**row, "gate": "below threshold"})
                if len(corroborators) < self.min_corroborators or not anchored:
                    # Two weak witnesses — an old registrant in roughly the
                    # right industry — are the signature of a namesake, not
                    # of the same company. Without one witness that names
                    # the CIK itself, the row is staged for confirmation.
                    return Decision(
                        STAGED,
                        note=(f"only {len(corroborators)} corroborator(s)"
                              if len(corroborators) < self.min_corroborators
                              else "no CIK-anchored corroborator"),
                        row={**row, "gate": "under-corroborated"},
                    )
                return Decision(
                    ACCEPTED,
                    note="; ".join(witnesses),
                    row={
                        **row,
                        "override": {
                            "normalized_name": item["normalized_name"],
                            "scope_state": base["scope_state"],
                            "scope_year": base["scope_year"],
                            "scope_location": base["scope_location"],
                            "decision": "accept", "cik": cik, "ein": "", "lei": "",
                            "wikidata_qid": "",
                            "note": f"Matched as {used!r} ({cik_match}); "
                                    + "; ".join(witnesses) + f". {note}",
                        },
                    },
                )

        if parent_name or parent_cik:
            owner = self._owner(item)
            if not parent_name or self.matcher is None:
                return Decision(
                    STAGED,
                    note="parent name must resolve to a registrant",
                    row={**base, "gate": "unverifiable parent"},
                )
            resolved = self._matches(item, [parent_name])
            if len(resolved) != 1:
                return Decision(
                    STAGED,
                    note=f"parent {parent_name!r} did not resolve uniquely",
                    row={**base, "gate": "unverifiable parent"},
                )
            resolved_cik = next(iter(resolved))
            if parent_cik and parent_cik != resolved_cik:
                return Decision(
                    STAGED,
                    note=f"supplied parent CIK {parent_cik} disagrees with name CIK {resolved_cik}",
                    row={**base, "gate": "parent name/CIK conflict"},
                )
            parent_cik = resolved_cik
            if not owner or int(owner["parent_cik"]) != parent_cik:
                return Decision(
                    STAGED,
                    note="no matching SEC Exhibit 21 ownership evidence",
                    row={**base, "gate": "unsupported parent"},
                )
            years = [int(y) for y in item.get("years", []) if str(y).isdigit()]
            try:
                source_year = int(owner.get("source_year") or 0)
            except (TypeError, ValueError):
                source_year = 0
            if not years or not source_year or any(abs(y - source_year) > 2 for y in years):
                return Decision(
                    STAGED,
                    note=f"Exhibit 21 year {source_year or 'unknown'} does not cover notice years",
                    row={**base, "gate": "ownership era uncertain"},
                )
            # Exact SEC names are already applied by the generated index.
            # Prefix lookup ("Cessna" -> "Cessna Aircraft Company") is a
            # useful candidate, but not enough to adjudicate an alias.
            if owner["normalized_name"] != item["normalized_name"]:
                return Decision(
                    STAGED,
                    note="Exhibit 21 lists a longer name; alias needs review",
                    row={**base, "gate": "non-exact subsidiary name"},
                )
            return Decision(ABSTAINED, note="SEC Exhibit 21 already links it", row=None)

        if matcher_refused:
            return Decision(STAGED, note=matcher_refused,
                            row={**base, "gate": "matcher refused"})

        if stance in EMPTY_STANCES and confidence >= self.threshold:
            # Recorded and staged, never written. The ledger stops the
            # re-asking; the staging row gives a person the queue of "the
            # model says nothing is there" claims, ranked by workers, to
            # promote to reject overrides deliberately. A wrong "private"
            # written here would be the least-checked answer in the file
            # made the most permanent.
            return Decision(
                REJECTED,
                note=note or f"{stance}: no registrant to find",
                row={**base, "gate": "model-rejected"},
            )

        return Decision(ABSTAINED, note=note or stance or "no answer", row=None)


def _names(employer_name: str | None) -> list[str]:
    """The filed name and its company part, normalized — as annotate does."""
    norm = normalized_employer(employer_name)
    base = normalized_employer(base_employer(employer_name))
    return [n for n in (norm, base) if n]


def _same_sector(a: str, b: str) -> bool:
    """Whether two NAICS codes name the same sector.

    Compared at two digits because that is the level a WARN form's industry
    field is worth. Sector groups are spelled as ranges ("31-33"), so a
    code is in one when its first two digits are any member.
    """
    def sectors(code: str) -> set[str]:
        code = (code or "").strip()
        if "-" in code:
            lo, hi = code.split("-", 1)
            try:
                return {str(n) for n in range(int(lo), int(hi) + 1)}
            except ValueError:
                return {code}
        return {code[:2]}

    return bool(sectors(a) & sectors(b))


def write(rows: list[dict], overrides_path: Path = OVERRIDES_PATH,
          subsidiary_path: Path = SUBSIDIARY_OVERRIDES,
          staging_path: Path = STAGING_PATH,
          decided_by: str = "") -> tuple[int, int, int]:
    """Append identities and parent links; stage everything unproven.

    An employer already carrying a decision keeps it. A later run disagreeing
    with an earlier one is something to look at, not something to apply
    silently, so the conflict is staged and the existing row stands.
    """
    today = date.today().isoformat()
    decided = _existing(overrides_path)
    linked = _existing(subsidiary_path)

    identities, links, staged = [], [], []
    for row in rows:
        override, subsidiary = row.get("override"), row.get("subsidiary")
        if override and review.scope_tuple(override) not in decided:
            decided.add(review.scope_tuple(override))
            identities.append({
                **override, "decided_by": decided_by, "decided_at": today,
            })
        elif subsidiary and review.scope_tuple(subsidiary) not in linked:
            linked.add(review.scope_tuple(subsidiary))
            links.append({
                **subsidiary, "decision": "accept",
                "decided_by": decided_by, "decided_at": today,
            })
        elif override or subsidiary:
            staged.append({**row, "gate": "already decided"})
        else:
            staged.append(row)

    _append(overrides_path, OVERRIDE_FIELDS, identities)
    _append(subsidiary_path, SUBSIDIARY_FIELDS, links)

    if staged:
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        with open(staging_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=STAGING_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(staged, key=lambda r: -int(r.get("workers") or 0)):
                writer.writerow({**row, "outcome": row.get("_outcome", "")})

    return len(identities), len(links), len(staged)


def _existing(path: Path) -> set[tuple[str, str, str, str]]:
    if not path.exists():
        return set()
    with open(path, newline="") as fh:
        return {review.scope_tuple(r) for r in csv.DictReader(fh)
                if r.get("normalized_name")}


def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        with open(path, newline="") as fh:
            reader = csv.DictReader(fh)
            old_fields = reader.fieldnames or []
            if old_fields != fields:
                existing = list(reader)
                # Header upgrades preserve every old column and row. Rename
                # in the same directory so replacement is atomic.
                all_fields = old_fields + [f for f in fields if f not in old_fields]
                fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
                try:
                    with os.fdopen(fd, "w", newline="") as tmp:
                        writer = csv.DictWriter(tmp, fieldnames=all_fields)
                        writer.writeheader()
                        writer.writerows(existing)
                        writer.writerows(rows)
                    os.replace(temp_name, path)
                finally:
                    if os.path.exists(temp_name):
                        os.unlink(temp_name)
                return
    write_header = not path.exists()
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
