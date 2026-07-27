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
- a subsidiary becomes a parent link, claiming only that somebody owns it —
  First Transit is not FirstGroup, and writing FirstGroup's CIK into its
  identity would conflate them in every join made afterwards;
- everything else becomes a recorded rejection, which grants nothing and
  stops the same employer being re-decided every run.

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
from warnlive.enrich.review import OVERRIDES_PATH, REVIEW_PATH, load_overrides
from warnlive.normalize.engine import base_employer, normalized_employer

logger = logging.getLogger("warnlive")

STAGING_PATH = Path("data/health/identity_adjudicated.csv")
SUBSIDIARY_OVERRIDES = subsidiaries.OVERRIDES_PATH

OVERRIDE_FIELDS = [
    "normalized_name", "decision", "cik", "ein", "lei", "wikidata_qid",
    "decided_by", "decided_at", "note",
]
SUBSIDIARY_FIELDS = [
    "normalized_name", "parent_cik", "parent_name", "source_year",
    "decided_by", "decided_at", "note",
]
STAGING_FIELDS = [
    "normalized_name", "employer_name", "states", "notices", "workers", "years",
    "stance", "proposed", "matched_cik", "cik_match", "parent_name", "parent_cik",
    "confidence", "corroborated_by", "outcome", "gate", "note",
]

# Stances that assert nothing is there to find. Recorded as rejections so the
# employer stops returning to the queue; they grant no identity.
EMPTY_STANCES = {"private", "government", "franchise", "nonexistent"}
MIN_CORROBORATORS = 2

SYSTEM = """\
You are identifying the employers named on US WARN layoff notices. Each row \
is one employer exactly as states filed it, with where and when it filed and \
how many workers its notices covered. Some rows carry candidate SEC \
registrants an automatic matcher found and then refused, with the reason.

For each row, say who the employer is:

- "stance": one of "public" (the employer itself files with the SEC), \
"subsidiary" (it is owned by a company that does), "private", "government", \
"franchise" (an independently owned outlet of a chain), or "unknown".
- "proposed": names under which this employer might be registered with the \
SEC, most likely first, at most three. Give the registrant's own legal name, \
not the filed name. Empty unless stance is "public".
- "parent_name" and "parent_cik": the corporate parent, when stance is \
"subsidiary". Give the CIK only if you are sure of it; otherwise leave it 0.
- "confidence": 0 to 1.
- "note": the evidence, in one short sentence.

Most WARN employers are private companies, franchises, school districts, \
hospitals and staffing firms with no SEC registration at all. Answering \
"private" or "government" is a real answer and is expected to be common. \
Answer "unknown" rather than guessing. Never invent a CIK.

Names carry the site as well as the company — "Ford Motor Co. - Flat Rock", \
"KMART - STORE #3671". Identify the company, and ignore the site. Where a \
row has a "company" field, the site has already been cut off for you; use \
it, and go back to the filed name only if the cut looks wrong.

The company name has already been searched against the SEC's register of \
filers and not found. Propose the other names this same company might be \
registered under: its full legal name, a former name, the name of the \
entity that actually files for it. Do not repeat a name that was already \
searched — the one in "company" — unless you believe the search was wrong.

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
        got = annotator.annotate(row["employer_name"], row["d"], row["fields_json"])
        if got["cik"] or got["ein"] or got["lei"] or got["wikidata_qid"]:
            continue
        entry = employers.setdefault(norm, {
            "normalized_name": norm,
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

    for name, candidates in _review_candidates().items():
        if name in employers:
            employers[name]["candidates"] = candidates

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
    #: v2 shows the deterministically cleaned company name and says it has
    #: already been searched, which turns "who is this employer" into "what
    #: else might this company be registered as". A different question, so a
    #: different version: v1's answers stay for comparison and are not
    #: replayed as though they answered this one.
    prompt_version = "identity-v2"
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
        self.overrides = load_overrides()
        self._spans: dict[int, tuple[int, int]] | None = None

    def system(self) -> str:
        return SYSTEM

    def key(self, item: dict) -> str:
        return item["normalized_name"]

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
        if item.get("sites"):
            out["sites"] = item["sites"][:4]
        if item.get("candidates"):
            out["refused_candidates"] = [
                f"{c['name']} (CIK {c['cik']}, {c['rejected_by']})"
                for c in item["candidates"]
            ]
        return out

    # -- gates ------------------------------------------------------------

    def _match(self, item: dict, proposed: list[str]) -> tuple[int | None, str, str]:
        """First proposed name that clears the unmodified EDGAR matcher.

        The matcher is not relaxed for a proposal. A name the model supplies
        has to survive the same era, token-compatibility and uniqueness rules
        that refused the filed name, so a wrong guess dies here rather than
        arriving as a confident match.
        """
        if self.matcher is None:
            return None, "", ""
        year = int(item["years"][-1]) if item.get("years") else None
        for name in proposed:
            name = (name or "").strip()
            if not name:
                continue
            hit = self.matcher.match(name, year)
            if hit:
                return hit[0], hit[2], name
        return None, "", ""

    def _owner(self, item: dict) -> dict | None:
        """Whose Exhibit 21 lists this employer, under either of its names."""
        return next(
            (o for o in (
                self.subsidiaries.parent(n) for n in _names(item["employer_name"])
            ) if o),
            None,
        )

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
            hit = self.subsidiaries.by_name.get(name)
            if hit:
                return hit
        return None

    def _corroborate(self, item: dict, cik: int, cik_match: str,
                     parent_cik: int | None) -> list[str]:
        """Evidence for a matched CIK that the proposal itself did not supply.

        Each of these is an independent authority: the filing calendar, the
        parent's own Exhibit 21, the industry the state published, and the
        rosters the other identity tiers are built from. Agreement between
        two of them is a much stronger claim than a name that merely matched.
        """
        found: list[str] = []

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
            found.append(f"registrant filed {span[0]}-{span[1]}, covering the notices")

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
                found.append(f"Wikidata knows CIK {cik} as {entity['label']!r}")

        # Exhibit 21 of the proposed parent's 10-K lists this employer.
        #
        # Only the parent's filing counts, and only when the parent is not
        # the registrant being claimed. Exhibit 21 says "X owns this", which
        # corroborates ownership and argues *against* identity: an employer
        # listed as a subsidiary of the very CIK being assigned to it is a
        # subsidiary of that company, not that company. Counting it either
        # way would be the First Transit conflation with extra steps.
        owner = self._owner(item)
        if owner and parent_cik and parent_cik != cik:
            if int(owner["parent_cik"]) == parent_cik:
                found.append(f"Exhibit 21 of {owner['parent_name']} lists it")

        # The industry the states published for this employer agrees with the
        # industry the SEC assigned the registrant.
        sic = self.annotator.sic_by_cik.get(cik, ("", ""))[0]
        sec_naics = self.annotator.naics_by_sic.get(sic)
        if sec_naics and item.get("source_naics"):
            if any(_same_sector(sec_naics, n) for n in item["source_naics"]):
                found.append("SEC industry agrees with the state-reported one")

        # The IRS or GLEIF roster knows this name, in a state it filed from.
        for name in _names(item["employer_name"]):
            org = self.annotator.nonprofit_by_name.get(name)
            if org and org.get("state") in set(item["states"]):
                found.append("IRS roster has it in a state it filed from")
                break
            entity = self.annotator.gleif_by_name.get(name)
            if entity and entity.get("jurisdiction", "")[-2:] in set(item["states"]):
                found.append("GLEIF has it in a state it filed from")
                break

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
        if item["normalized_name"] in self.overrides:
            return Decision(STAGED, note="already decided by hand",
                            row={**base, "gate": "existing override"})

        if stance == "public" and proposed:
            cik, cik_match, used = self._match(item, proposed)
            if not cik:
                return Decision(
                    STAGED,
                    note=f"no proposed name cleared the matcher: {', '.join(proposed)}",
                    row={**base, "gate": "matcher refused"},
                )
            # Exhibit 21 of the matched registrant listing this employer
            # contradicts the claim being made: a company does not appear in
            # its own subsidiary schedule. The stance is wrong, and the fix
            # is a parent link rather than an identity, so a person decides.
            owner = self._listed_exactly(item)
            if owner and int(owner["parent_cik"]) == cik:
                return Decision(
                    STAGED,
                    note=f"Exhibit 21 of {owner['parent_name']} lists it as a "
                         "subsidiary, not as the registrant",
                    row={**base, "matched_cik": cik, "cik_match": cik_match,
                         "gate": "listed as its own subsidiary"},
                )
            corroborators = self._corroborate(item, cik, cik_match, parent_cik)
            row = {
                **base, "matched_cik": cik, "cik_match": cik_match,
                "corroborated_by": "; ".join(corroborators),
            }
            if confidence < self.threshold:
                return Decision(STAGED, note=f"confidence {confidence:.2f}",
                                row={**row, "gate": "below threshold"})
            if len(corroborators) < self.min_corroborators:
                return Decision(
                    STAGED,
                    note=f"only {len(corroborators)} corroborator(s)",
                    row={**row, "gate": "under-corroborated"},
                )
            return Decision(
                ACCEPTED,
                note="; ".join(corroborators),
                row={
                    **row,
                    "override": {
                        "normalized_name": item["normalized_name"],
                        "decision": "", "cik": cik, "ein": "", "lei": "",
                        "wikidata_qid": "",
                        "note": f"Matched as {used!r} ({cik_match}); "
                                + "; ".join(corroborators) + f". {note}",
                    },
                },
            )

        if stance == "subsidiary" and (parent_name or parent_cik):
            owner = self._owner(item)
            if owner:
                # Exhibit 21 already says this, and the generated table
                # already applies it. Nothing to write.
                return Decision(ABSTAINED, note="Exhibit 21 already links it", row=None)
            # A parent's CIK is ours to look up, not the model's to recall.
            #
            # Asking for a nine-digit number is asking for the one thing a
            # language model is worst at and the roster is best at, and the
            # first two hundred rows showed it: forty-three of seventy stalls
            # were a correctly-named parent with a missing or invented CIK —
            # First Transit, DHL Supply Chain, USS Posco among them. So the
            # name goes through the same matcher a proposed registrant does.
            # That is the design already stated: propose a name, never a fact.
            # A number the model supplies is still checked, and still has to
            # be one the reference files have heard of.
            looked_up = ""
            if not parent_cik and parent_name and self.matcher is not None:
                year = int(item["years"][-1]) if item.get("years") else None
                hit = self.matcher.match(parent_name, year)
                if hit:
                    parent_cik, looked_up = hit[0], hit[2]
            if not parent_cik:
                return Decision(
                    STAGED,
                    note=f"parent {parent_name!r} matched no registrant",
                    row={**base, "gate": "unverifiable parent"},
                )
            if parent_cik not in self.annotator.sic_by_cik and (
                self.matcher is None or not self.matcher.ticker_for(parent_cik)
            ):
                # A CIK nobody in the reference files has ever seen is a
                # number, not a company.
                return Decision(
                    STAGED,
                    note=f"CIK {parent_cik} is in no reference file",
                    row={**base, "gate": "unknown parent CIK"},
                )
            if confidence < self.threshold:
                return Decision(STAGED, note=f"confidence {confidence:.2f}",
                                row={**base, "gate": "below threshold"})
            how = f" (matched {looked_up})" if looked_up else ""
            return Decision(
                ACCEPTED,
                note=f"parent {parent_name} (CIK {parent_cik}){how}",
                row={
                    **base, "parent_cik": parent_cik,
                    "subsidiary": {
                        "normalized_name": item["normalized_name"],
                        "parent_cik": parent_cik,
                        "parent_name": parent_name,
                        "source_year": "",
                        "note": f"{note} Parent CIK from the EDGAR roster"
                                f" ({looked_up})." if looked_up else note,
                    },
                },
            )

        if stance in EMPTY_STANCES and confidence >= self.threshold:
            return Decision(
                REJECTED,
                note=note or f"{stance}: no registrant to find",
                row={
                    **base,
                    "override": {
                        "normalized_name": item["normalized_name"],
                        "decision": "reject", "cik": "", "ein": "", "lei": "",
                        "wikidata_qid": "",
                        "note": f"{stance}: {note}" if note else stance,
                    },
                },
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
    decided = _existing(overrides_path, "normalized_name")
    linked = _existing(subsidiary_path, "normalized_name")

    identities, links, staged = [], [], []
    for row in rows:
        override, subsidiary = row.get("override"), row.get("subsidiary")
        if override and override["normalized_name"] not in decided:
            decided.add(override["normalized_name"])
            identities.append({
                **override, "decided_by": decided_by, "decided_at": today,
            })
        elif subsidiary and subsidiary["normalized_name"] not in linked:
            linked.add(subsidiary["normalized_name"])
            links.append({
                **subsidiary, "decided_by": decided_by, "decided_at": today,
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


def _existing(path: Path, column: str) -> set[str]:
    if not path.exists():
        return set()
    with open(path, newline="") as fh:
        return {r[column] for r in csv.DictReader(fh) if r.get(column)}


def _append(path: Path, fields: list[str], rows: list[dict]) -> None:
    if not rows:
        return
    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fields})
