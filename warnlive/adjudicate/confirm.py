"""The second look at a match nothing else could corroborate.

A proposed name that clears the EDGAR matcher still has to be corroborated
by evidence the proposal never saw, and most are: the filing calendar, a
parent's Exhibit 21, the state-published industry, the IRS and GLEIF
rosters. Some are not, and until now those were staged and left — which for
an obscure employer means left forever, because the reason nothing
corroborates it is usually that nothing else knows about it either.

Staging them was the safe choice and the wrong one. The matcher already
found a specific registrant; the question is only whether that registrant is
this employer, and that is answerable by looking. So the match is put back
to the model as a yes-or-no about one named company, with everything the
roster knows about it — legal name, filing years, SEC industry, ticker —
next to everything the notices say: where the layoffs were, when, how many
people, what industry the state called it.

This is a narrower question than the one that produced the proposal, and
narrower is the point. "Which SEC registrant is this employer" is answered
out of memory across ten thousand filers. "Is Midway Airlines of Chicago,
which stopped flying in 1991, the same company as the registrant that began
filing in 1997" is answered by reading two dates. The first question is
where a model guesses; the second is where it is useful.

It runs only where deterministic corroboration fell short, and it
supplements evidence rather than replacing it: a yes from the model is
accepted only where at least one independent witness already spoke.
A match with no corroborator at all stays staged for a person — the model
confirming its own family's proposal, over the same roster facts the
deterministic checks already found wanting, is not a second witness.

For the same reason, run this queue with a different model than the one
that proposed the matches (--model / --provider): the proposer and the
confirmer sharing a family share their misconceptions, and the command
warns when they are the same.
"""

from __future__ import annotations

import logging
from pathlib import Path

from warnlive.adjudicate.identity import Identity
from warnlive.adjudicate.queue import ACCEPTED, REJECTED, STAGED, Decision

logger = logging.getLogger("warnlive")

# Confirm's own review file. Identity's write() rewrites whatever staging
# path it is given, and this queue is a handful of rows — pointed at the
# identity staging file it would replace two hundred rows of review with
# them.
STAGING_PATH = Path("data/health/identity_confirm_adjudicated.csv")

SYSTEM = """\
You are checking whether an employer named on US WARN layoff notices is the \
same company as one specific SEC registrant. An automatic matcher proposed \
this registrant by name, and no independent record could confirm it, so the \
match stands or falls on whether the two are the same company.

Each row gives the employer as states filed it, the towns and years its \
notices cover, how many workers, and the industry the state reported; then \
the registrant, with the years it filed with the SEC, its SEC industry and \
its ticker.

For each row answer:

- "same": true if the registrant is this employer, false otherwise.
- "confidence": 0 to 1.
- "note": the deciding fact, in one short sentence.

Say false when the evidence does not fit. A registrant whose filings begin \
years after the employer shut down is a different company that later took \
the name. A registrant in an unrelated industry is a namesake. A registrant \
that is the employer's corporate parent is not the employer: it owns it, \
which is a different claim, and answering true would merge two companies.

Say true only for the same legal entity, or for a company filing under its \
own former or full legal name. Being unsure is a reason to answer false: an \
unconfirmed match costs nothing, and a wrong one is published as fact.

Reply with JSON only, in exactly this form:

{"results": [
  {"id": 1, "same": true, "confidence": 0.91,
   "note": "West Telemarketing filed as West Corporation from Omaha, matching the Nebraska notices"},
  {"id": 2, "same": false, "confidence": 0.94,
   "note": "the Chicago carrier ceased in 1991; this registrant first filed in 1997"}
]}"""


class Confirm(Identity):
    """Asks whether one named registrant is one named employer."""

    task = "identity-confirm"
    prompt_version = "confirm-v1"
    required = {"same": bool, "confidence": (int, float)}
    batch_size = 10
    max_tokens_per_row = 120
    reasoning_tokens_per_row = 340

    def key(self, item: dict) -> str:
        """The employer and the registrant being asked about.

        A yes about one CIK is not a yes about another, so a re-run that
        proposes a different registrant must ask again rather than replay an
        answer given about a company it is no longer considering.
        """
        return f"{item['normalized_name']}|{item['matched_cik']}"

    def system(self) -> str:
        return SYSTEM

    def render(self, item: dict) -> dict:
        cik = item["matched_cik"]
        span = self._filing_span(cik)
        sic = self.annotator.sic_by_cik.get(cik, ("", ""))
        out = {
            "employer": item["employer_name"],
            "states": ",".join(item["states"][:8]),
            "years": f"{item['years'][0]}-{item['years'][-1]}" if item["years"] else "",
            "workers": item["workers"],
            "registrant": {
                "name": item.get("matched_name") or "",
                "filed": f"{span[0]}-{span[1]}" if span else "",
                "industry": sic[1] or "",
                "ticker": self.matcher.ticker_for(cik) if self.matcher else "",
            },
        }
        if item.get("cleaned_name"):
            out["company"] = item["cleaned_name"]
        if item.get("sites"):
            out["sites"] = item["sites"][:4]
        if item.get("source_naics"):
            out["reported_industry"] = ", ".join(item["source_naics"][:3])
        return out

    def decide(self, item: dict, answer: dict) -> Decision:
        same = bool(answer.get("same"))
        try:
            confidence = float(answer.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        note = str(answer.get("note") or "").strip()[:300]
        cik = item["matched_cik"]

        base = {
            "normalized_name": item["normalized_name"],
            "employer_name": item["employer_name"],
            "states": "|".join(item["states"]),
            "notices": item["notices"],
            "workers": item["workers"],
            "years": f"{item['years'][0]}-{item['years'][-1]}" if item["years"] else "",
            "matched_cik": cik,
            "cik_match": item.get("cik_match") or "",
            "proposed": item.get("matched_name") or "",
            "confidence": round(confidence, 3),
            "note": note,
        }

        if item["normalized_name"] in self.overrides:
            return Decision(STAGED, note="already decided by hand",
                            row={**base, "gate": "existing override"})

        if not same:
            # A refused match is not a refused employer: some other
            # registrant may still be the right one, and saying otherwise
            # would close the case on evidence about one candidate.
            return Decision(
                REJECTED,
                note=note or "not the same company",
                row={**base, "gate": "confirmed not a match"},
            )

        if confidence < self.threshold:
            return Decision(STAGED, note=f"confidence {confidence:.2f}",
                            row={**base, "gate": "below threshold"})

        # The contradiction check still applies. Exhibit 21 listing this
        # employer under the matched registrant means the registrant owns it
        # rather than is it, and no amount of confirmation changes that.
        owner = self._listed_exactly(item)
        if owner and int(owner["parent_cik"]) == cik:
            return Decision(
                STAGED,
                note=f"Exhibit 21 of {owner['parent_name']} lists it as a subsidiary",
                row={**base, "gate": "listed as its own subsidiary"},
            )

        # Confirmation supplements evidence; it does not replace it. With no
        # independent witness at all, a yes here would rest entirely on the
        # model's word — the exact thing the identity gate refuses.
        corroborated_by = (item.get("corroborated_by") or "").strip()
        if not corroborated_by:
            return Decision(
                STAGED,
                note="confirmed by model, but no independent corroborator",
                row={**base, "gate": "no corroborator"},
            )

        return Decision(
            ACCEPTED,
            note=f"confirmed: {note}",
            row={
                **base,
                "override": {
                    "normalized_name": item["normalized_name"],
                    "decision": "", "cik": cik, "ein": "", "lei": "",
                    "wikidata_qid": "",
                    "note": f"Matched as {item.get('matched_name')!r} "
                            f"({item.get('cik_match')}); {corroborated_by}; "
                            f"confirmed by model: {note}",
                },
            },
        )


def load_queue(conn, ledger, model: str, min_workers: int = 0,
               limit: int | None = None) -> list[dict]:
    """Matches that cleared the matcher and nothing could corroborate.

    Built by replaying the identity ledger through today's gate rather than
    by reading the staging file, so the queue reflects the gate as it stands
    — a corroborator added since the answers were bought moves rows out of
    here without anyone having to remember to rebuild a CSV.
    """
    from warnlive.adjudicate import identity as identity_mod

    worker = identity_mod.Identity()
    items = identity_mod.load_queue(conn, min_workers=min_workers)
    out = []
    for item in items:
        entry = ledger.get(
            worker.task, worker.key(item), worker.prompt_version, model
        )
        if entry is None:
            continue
        decision = worker.decide(item, entry.answer or {})
        row = decision.row or {}
        if row.get("gate") != "under-corroborated":
            continue
        out.append({
            **item,
            "matched_cik": int(row["matched_cik"]),
            "matched_name": (row.get("proposed") or "").split("|")[0],
            "cik_match": row.get("cik_match") or "",
            "corroborated_by": row.get("corroborated_by") or "",
        })
        if limit and len(out) >= limit:
            break
    return out
