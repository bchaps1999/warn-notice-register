"""What industry an employer is in, when no authority has said.

Six bases already supply industry, in order of how directly they say it: the
state's own NAICS code, an official sector name it printed, the SIC the SEC
assigned a matched registrant, the IRS activity code for an exempt
organization, the parent's industry, and what the same employer reported on
another notice. Together they cover about a third of notices. The rest are
employers nobody has classified, and a name is usually enough for a person to
say — "Crothall Healthcare" is health care, "Gate Gourmet" is food service.

This queue differs from the other two in one uncomfortable way: there is no
independent authority to check an answer against. An alias can be handed back
to the resolver and a registrant to the matcher, but nothing can be handed a
sector and asked whether it is right. So the gate here is a threshold, and a
threshold nobody measured is a number somebody made up.

Hence calibration. Sixteen thousand notices already carry an industry the
state itself published. Hiding it, classifying from the name alone, and
scoring against it gives a precision-at-coverage curve over exactly the task
being asked — from labels that already exist, for the price of one run.
The threshold comes off that curve. Employers are scored, never notices:
First Transit alone files forty-nine times, and counting it forty-nine times
would measure how often big employers file rather than how often the model is
right.
"""

from __future__ import annotations

import csv
import logging
import random
from collections import defaultdict
from datetime import date
from pathlib import Path

from warnlive.adjudicate.queue import (
    ABSTAINED,
    ACCEPTED,
    STAGED,
    Adjudicator,
    Decision,
)
from warnlive.enrich.annotate import Annotator
from warnlive.enrich.industry import (
    OVERRIDES_PATH,
    industry_from_fields_json,
    load_industry_overrides,
)
from warnlive.normalize.engine import normalized_employer

logger = logging.getLogger("warnlive")

STAGING_PATH = Path("data/health/industry_adjudicated.csv")
CALIBRATION_PATH = Path("data/health/industry_calibration.csv")

OVERRIDE_FIELDS = ["normalized_name", "naics", "decided_by", "decided_at", "note"]
STAGING_FIELDS = [
    "normalized_name", "employer_name", "states", "notices", "workers",
    "naics", "sector_name", "confidence", "outcome", "gate", "note",
]

# The twenty NAICS sectors as the exports spell them. Sector groups keep their
# range form, because that is what the source-published codes crosswalk to.
SECTORS = {
    "11": "Agriculture, Forestry, Fishing and Hunting",
    "21": "Mining, Quarrying, and Oil and Gas Extraction",
    "22": "Utilities",
    "23": "Construction",
    "31-33": "Manufacturing",
    "42": "Wholesale Trade",
    "44-45": "Retail Trade",
    "48-49": "Transportation and Warehousing",
    "51": "Information",
    "52": "Finance and Insurance",
    "53": "Real Estate and Rental and Leasing",
    "54": "Professional, Scientific, and Technical Services",
    "55": "Management of Companies and Enterprises",
    "56": "Administrative and Support and Waste Management and Remediation Services",
    "61": "Educational Services",
    "62": "Health Care and Social Assistance",
    "71": "Arts, Entertainment, and Recreation",
    "72": "Accommodation and Food Services",
    "81": "Other Services (except Public Administration)",
    "92": "Public Administration",
}
# Members of a grouped sector, so a bare "32" is understood as Manufacturing.
_GROUPED = {
    "31": "31-33", "32": "31-33", "33": "31-33",
    "44": "44-45", "45": "44-45",
    "48": "48-49", "49": "48-49",
}

PROMPTS_DIR = Path(__file__).with_name("prompts")
DEFAULT_PROMPT = "industry-v3"


def load_prompt(name: str = DEFAULT_PROMPT) -> str:
    """One named prompt, with the sector list filled in.

    Kept as files rather than a constant so comparing two is a flag and not
    an edit. A prompt edited in place has no past: the ledger keys answers by
    version, so two variants must be two names or the older answers are
    silently reused under the newer name.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    if not path.exists():
        have = ", ".join(sorted(p.stem for p in PROMPTS_DIR.glob("*.txt")))
        raise FileNotFoundError(f"no prompt {name!r}; prompts/ has {have}")
    # A plain substitution, not str.format: the prompt ends in a JSON example
    # and every brace in it would have to be doubled to survive formatting.
    return path.read_text().replace(
        "{sectors}",
        "\n".join(f"  {code}  {label}" for code, label in SECTORS.items()),
    ).rstrip("\n")


def _employers(conn, annotator: Annotator, want_labelled: bool) -> list[dict]:
    """Employers grouped from notices, with or without a published industry.

    One pass serves both callers: the queue wants employers no basis reached,
    calibration wants the ones a state labelled itself. An employer whose
    notices disagree about its industry is dropped from calibration — the
    same rule annotate.prime() applies, since a conflict is either a misparse
    or a genuinely diversified filer and neither is a label.
    """
    employers: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT n.employer_name AS employer_name, n.state AS state, "
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
        _, source_naics, basis = industry_from_fields_json(row["fields_json"])

        if want_labelled:
            # Only what the state itself published counts as a label; a code
            # this pipeline derived would be marking its own homework.
            if not source_naics or basis not in ("source", "sector-name", "sic-crosswalk"):
                continue
        elif got["naics"]:
            continue

        entry = employers.setdefault(norm, {
            "normalized_name": norm, "employer_name": row["employer_name"],
            "states": set(), "notices": 0, "workers": 0, "labels": set(),
        })
        entry["notices"] += 1
        entry["workers"] += row["jobs"] or 0
        if row["state"]:
            entry["states"].add(row["state"])
        if source_naics:
            entry["labels"].add(sector_of(source_naics))

    rows = []
    for e in employers.values():
        if want_labelled:
            labels = {label for label in e["labels"] if label}
            if len(labels) != 1:
                continue
            e["truth"] = next(iter(labels))
        e["states"] = sorted(e["states"])
        e.pop("labels", None)
        rows.append(e)
    rows.sort(key=lambda r: (-r["workers"], r["normalized_name"]))
    return rows


def load_queue(conn, min_workers: int = 0, limit: int | None = None) -> list[dict]:
    """Employers no industry basis reached, worst first."""
    rows = [
        r for r in _employers(conn, Annotator(), want_labelled=False)
        if r["workers"] >= min_workers
    ]
    return rows[:limit] if limit else rows


def load_calibration(conn, min_workers: int = 0, limit: int | None = None,
                     sample: int | None = None, seed: int = 0,
                     split: str = "tune") -> list[dict]:
    """Employers a state published an industry for — the held-out labels.

    Sampled at random rather than taken from the top, because the queue this
    calibrates is not made of famous companies. The labelled set is ordered
    by workers like every other queue here, so its head is United Airlines
    and Walt Disney Parks — employers any model classifies correctly. A
    threshold measured on those and applied to forty thousand small unknown
    ones would be measuring the wrong thing and reporting it confidently.

    The sample is seeded, so a rerun grades the same employers and two runs
    are comparable.

    The labelled employers are split in half and the halves never mix.
    Prompts are compared on "tune"; "test" is scored once, at the end, by
    whichever prompt won. Reading a confusion table and editing the prompt to
    fix what it showed is fitting to that sample, and scoring the edit on the
    same sample measures the fit rather than the prompt — the number only
    goes up, and it stops meaning anything. Half of eleven thousand is ample
    for both.

    One caveat no splitting fixes: these labels exist because seventeen states
    publish an industry, and employers filing in those states are not a
    random draw from all employers. Precision here is an estimate for
    labelled employers, and the unlabelled queue may well be harder.
    """
    if split not in ("tune", "test"):
        raise ValueError(f"split must be 'tune' or 'test', not {split!r}")
    rows = [
        r for r in _employers(conn, Annotator(), want_labelled=True)
        if r["workers"] >= min_workers
    ]
    # Shuffled from a fixed order so the halves are stable across runs and
    # across machines, and disjoint by construction rather than by luck.
    rows.sort(key=lambda r: r["normalized_name"])
    random.Random(seed).shuffle(rows)
    half = len(rows) // 2
    rows = rows[:half] if split == "tune" else rows[half:]
    if sample and sample < len(rows):
        rows = rows[:sample]
    rows.sort(key=lambda r: (-r["workers"], r["normalized_name"]))
    return rows[:limit] if limit else rows


def sector_of(naics: str | None) -> str:
    """The 2-digit sector a NAICS code belongs to, in its canonical spelling."""
    code = (naics or "").strip()
    if not code:
        return ""
    if code in SECTORS:
        return code
    head = code.split("-", 1)[0][:2]
    return _GROUPED.get(head, head if head in SECTORS else "")


class Industry(Adjudicator):
    """Assigns a sector; the threshold that admits it comes from calibration."""

    task = "industry"
    prompt_version = "industry-v3"
    required = {"naics": str, "confidence": (int, float)}
    batch_size = 20
    max_tokens_per_row = 70

    def __init__(self, threshold: float = 0.9,
                 prompt: str = DEFAULT_PROMPT) -> None:
        self.threshold = threshold
        # The prompt names the version, so the ledger keys answers by which
        # instructions produced them and two variants never blend.
        self.prompt_version = prompt
        self._system = load_prompt(prompt)

    def system(self) -> str:
        return self._system

    def key(self, item: dict) -> str:
        return item["normalized_name"]

    def render(self, item: dict) -> dict:
        return {
            "employer": item["employer_name"],
            "states": ",".join(item["states"][:6]),
            "workers": item["workers"],
        }

    def read(self, answer: dict) -> tuple[str, float, str]:
        """The sector, confidence and note in an answer, canonicalised."""
        sector = sector_of(str(answer.get("naics") or ""))
        try:
            confidence = float(answer.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return sector, confidence, str(answer.get("note") or "").strip()[:200]

    def decide(self, item: dict, answer: dict) -> Decision:
        sector, confidence, note = self.read(answer)
        base = {
            "normalized_name": item["normalized_name"],
            "employer_name": item["employer_name"],
            "states": "|".join(item["states"]),
            "notices": item["notices"],
            "workers": item["workers"],
            "naics": sector,
            "sector_name": SECTORS.get(sector, ""),
            "confidence": round(confidence, 3),
            "note": note,
        }
        if not sector:
            return Decision(ABSTAINED, note=note or "no sector given", row=None)
        if confidence < self.threshold:
            return Decision(
                STAGED,
                note=f"{sector} at confidence {confidence:.2f}",
                row={**base, "gate": f"below threshold {self.threshold}"},
            )
        return Decision(
            ACCEPTED,
            note=f"{sector} {SECTORS.get(sector, '')}",
            row={
                **base,
                "override": {
                    "normalized_name": item["normalized_name"],
                    "naics": sector,
                    "note": note,
                },
            },
        )


def score(items: list[dict], adj: Industry, ledger, model: str) -> list[dict]:
    """Precision at each confidence cut, against the states' own labels.

    Coverage is the share of employers the model answered at all at that cut;
    precision is how many of those it got right. The pair is what a threshold
    should be chosen from — accuracy alone hides that a model which answers
    everything and a model which answers a confident third are different
    instruments.
    """
    by_key = {i["normalized_name"]: i for i in items}
    graded = []
    for (task, key, version, model_slug), entry in ledger.entries.items():
        # Filtered by prompt version as well as model: two prompts in the
        # ledger would otherwise be graded together and reported as one
        # curve, with every employer counted twice.
        if (task != adj.task or model_slug != model
                or version != adj.prompt_version or key not in by_key):
            continue
        sector, confidence, _ = adj.read(entry.answer)
        graded.append({
            "normalized_name": key,
            "employer_name": by_key[key]["employer_name"],
            "workers": by_key[key]["workers"],
            "truth": by_key[key]["truth"],
            "predicted": sector,
            "confidence": confidence,
            "correct": int(bool(sector) and sector == by_key[key]["truth"]),
        })

    if not graded:
        # Silence here once produced a full curve off a previous prompt's
        # answers, reported as though it were this one's. A calibration that
        # graded nothing is a failed calibration, and must say so.
        raise RuntimeError(
            f"nothing to grade for {adj.task} at {adj.prompt_version} on "
            f"{model or '(no model)'}: no answers in the ledger under this "
            "prompt version. Run without --dry-run, or pass --reask if the "
            "rows were settled under a different prompt."
        )

    total = len(graded)
    total_workers = sum(g["workers"] for g in graded) or 1
    curve = []
    for cut in (0.0, 0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95, 0.99):
        kept = [g for g in graded if g["predicted"] and g["confidence"] >= cut]
        if not kept:
            continue
        correct = sum(g["correct"] for g in kept)
        w_correct = sum(g["workers"] for g in kept if g["correct"])
        curve.append({
            "threshold": cut,
            "answered": len(kept),
            "coverage": round(len(kept) / total, 4) if total else 0.0,
            "precision": round(correct / len(kept), 4),
            "worker_coverage": round(
                sum(g["workers"] for g in kept) / total_workers, 4
            ),
            "worker_precision": round(
                w_correct / (sum(g["workers"] for g in kept) or 1), 4
            ),
        })

    CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CALIBRATION_PATH, "w", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["threshold", "answered", "coverage", "precision",
                        "worker_coverage", "worker_precision"],
        )
        writer.writeheader()
        writer.writerows(curve)
    logger.info(
        "industry calibration: %d employers graded -> %s", total, CALIBRATION_PATH
    )
    return curve


def confusions(items: list[dict], adj: Industry, ledger, model: str, top: int = 10):
    """The sector pairs the model most often confuses, worst first."""
    by_key = {i["normalized_name"]: i for i in items}
    pairs: dict[tuple[str, str], int] = defaultdict(int)
    for (task, key, version, slug), entry in ledger.entries.items():
        if (task != adj.task or slug != model
                or version != adj.prompt_version or key not in by_key):
            continue
        sector, _c, _n = adj.read(entry.answer)
        truth = by_key[key]["truth"]
        if sector and sector != truth:
            pairs[(truth, sector)] += 1
    return sorted(pairs.items(), key=lambda kv: -kv[1])[:top]


def write(rows: list[dict], overrides_path: Path = OVERRIDES_PATH,
          staging_path: Path = STAGING_PATH, decided_by: str = "") -> tuple[int, int]:
    """Append accepted sectors; stage the rest. Existing decisions stand."""
    today = date.today().isoformat()
    decided: set[str] = set()
    if overrides_path.exists():
        with open(overrides_path, newline="") as fh:
            decided = {
                r["normalized_name"] for r in csv.DictReader(fh)
                if r.get("normalized_name")
            }

    accepted, staged = [], []
    for row in rows:
        override = row.get("override")
        if override and override["normalized_name"] not in decided:
            decided.add(override["normalized_name"])
            accepted.append({**override, "decided_by": decided_by, "decided_at": today})
        else:
            staged.append(row)

    if accepted:
        write_header = not overrides_path.exists()
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        with open(overrides_path, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=OVERRIDE_FIELDS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerows(accepted)

    if staged:
        staging_path.parent.mkdir(parents=True, exist_ok=True)
        with open(staging_path, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=STAGING_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in sorted(staged, key=lambda r: -int(r.get("workers") or 0)):
                writer.writerow({**row, "outcome": row.get("_outcome", "")})

    return len(accepted), len(staged)
