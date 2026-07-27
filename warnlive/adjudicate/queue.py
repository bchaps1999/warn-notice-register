"""The part every adjudicator has in common: ask, replay, tally.

Each of the three queues — places, identity, industry — differs only in what
it puts in the prompt and what evidence it makes an answer survive. The
mechanics around that are identical, and live here: rank the work, skip what
has already been settled, batch what is left, map answers back to the rows
that asked for them, and hand each one to the gate that judges it.

Two decisions in here are worth stating outright.

Answers are replayed through the gate, not around it. A row already in the
ledger is not re-asked, but its stored answer is re-judged by whatever the
gate says today. Gates get stricter as corroborators are added, and when
that happens every past answer should be re-examined without buying it
again.

An item the model does not answer stays unanswered. Batching means one call
carries many rows, and a reply that omits a row, or invents one, is not
evidence about it. Those rows simply return to the queue rather than being
guessed at from position.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from warnlive.adjudicate.client import (
    BudgetExceeded,
    Client,
    ModelRefused,
    shape_problem,
)
from warnlive.adjudicate.ledger import Entry, Ledger

logger = logging.getLogger("warnlive")

BATCH_SIZE = 10

# What a gate can conclude about one row.
ACCEPTED = "accepted"    # cleared its evidence; written to a reference file
STAGED = "staged"        # plausible, unproven; written to a review file
REJECTED = "rejected"    # examined and settled as having no answer
ABSTAINED = "abstained"  # the model declined, which is a valid answer
FAILED = "failed"        # no usable answer came back
OUTCOMES = (ACCEPTED, STAGED, REJECTED, ABSTAINED, FAILED)


@dataclass
class Decision:
    """What a gate concluded, and the row it wants written."""

    outcome: str
    note: str = ""
    row: dict | None = None

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")


@dataclass
class Tally:
    """What a run did, for the line it prints at the end."""

    seen: int = 0
    replayed: int = 0
    asked: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)
    stopped: str = ""

    def count(self, decision: Decision) -> None:
        self.by_outcome[decision.outcome] = self.by_outcome.get(decision.outcome, 0) + 1
        if decision.row is not None:
            self.rows.append({**decision.row, "_outcome": decision.outcome})

    def summary(self) -> str:
        parts = [f"{self.by_outcome.get(o, 0)} {o}" for o in OUTCOMES]
        return (
            f"{self.seen} rows ({self.replayed} replayed, {self.asked} asked): "
            + ", ".join(parts)
        )


class Adjudicator:
    """One queue's prompt and gate. Subclasses supply the four hooks."""

    task: str = ""
    prompt_version: str = ""
    #: keys the model must return per row, and the types they must hold
    required: dict[str, type | tuple[type, ...]] = {}
    batch_size: int = BATCH_SIZE
    #: room for the answer itself, per row
    max_tokens_per_row: int = 120
    #: room for the model's thinking, per row. Reasoning is billed as output
    #: and spent from the same allowance as the answer, and on this task it
    #: ran roughly three times the answer — budgeting for the answer alone
    #: truncates the JSON mid-string and loses the whole batch.
    reasoning_tokens_per_row: int = 260
    #: whether the model reasons before answering
    thinking: bool = True

    def room_for(self, rows: int) -> int:
        """How many output tokens a batch of this size may need."""
        per_row = self.max_tokens_per_row + (
            self.reasoning_tokens_per_row if self.thinking else 0
        )
        return per_row * rows + 512

    def system(self) -> str:
        """The fixed instructions. Must contain an example and the word JSON."""
        raise NotImplementedError

    def key(self, item: dict) -> str:
        """A stable identifier for this row, used as the ledger key."""
        raise NotImplementedError

    def render(self, item: dict) -> dict:
        """What the model is shown about this row."""
        raise NotImplementedError

    def decide(self, item: dict, answer: dict) -> Decision:
        """Judge one answer against evidence the model was not given."""
        raise NotImplementedError


def _batch_prompt(adj: Adjudicator, batch: list[dict]) -> str:
    """The variable half of the prompt: numbered rows, nothing else.

    Numbered rather than keyed so the identifier costs one token, and so an
    answer that names a row not in the batch is obviously wrong rather than
    plausibly about something else.
    """
    rows = [{"id": i, **adj.render(item)} for i, item in enumerate(batch, 1)]
    return json.dumps({"rows": rows}, ensure_ascii=False, indent=None)


def _answers_by_id(body: dict, size: int) -> dict[int, dict]:
    """Map a batch reply back to the rows that asked, dropping the rest."""
    out: dict[int, dict] = {}
    for entry in body.get("results") or []:
        if not isinstance(entry, dict):
            continue
        try:
            rid = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if 1 <= rid <= size and rid not in out:
            out[rid] = entry
    return out


def run(
    adj: Adjudicator,
    items: list[dict],
    client: Client | None = None,
    ledger: Ledger | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    reask: bool = False,
    model: str = "",
) -> Tally:
    """Work a queue: replay what is known, ask what is not, gate everything.

    `dry_run` never calls the API — it re-judges the ledger, which is how a
    change to a gate is checked before any money is spent. `reask` ignores an
    existing answer for the current prompt and model, for when a prompt has
    been edited in place.

    `model` names the model whose answers to replay. It is passed separately
    from the client because a dry run has no client and still has to look up
    the answers a real model gave: keying replay off the client would make
    --dry-run find nothing and silently report an empty queue.
    """
    ledger = ledger if ledger is not None else Ledger()
    tally = Tally()
    model_slug = str(client.model) if client else model

    pending: list[dict] = []
    for item in items:
        if limit is not None and tally.seen >= limit:
            break
        tally.seen += 1
        key = adj.key(item)
        known = (
            None
            if reask
            else ledger.get(adj.task, key, adj.prompt_version, model_slug)
        )
        # A row settled under an older prompt or a different model is left
        # alone unless this run can ask about it: skipping is what lets the
        # queues drain rather than being re-decided forever.
        if known is None and not reask and ledger.answered(
            adj.task, key, adj.prompt_version
        ):
            tally.seen -= 1
            continue
        if known is not None:
            tally.replayed += 1
            tally.count(adj.decide(item, known.answer))
            continue
        if dry_run or client is None:
            tally.seen -= 1
            continue
        pending.append(item)

    for start in range(0, len(pending), adj.batch_size):
        batch = pending[start : start + adj.batch_size]
        try:
            body = client.complete_json(
                adj.system(),
                _batch_prompt(adj, batch),
                required={"results": list},
                max_tokens=adj.room_for(len(batch)),
                thinking=adj.thinking,
            )
        except BudgetExceeded as exc:
            tally.stopped = str(exc)
            logger.warning("%s: %s", adj.task, exc)
            break
        except (ModelRefused, RuntimeError) as exc:
            # One unusable reply loses one batch, not the run. The rows stay
            # unrecorded, so the next run picks them up again.
            logger.warning("%s: batch failed: %s", adj.task, exc)
            for _ in batch:
                tally.count(Decision(FAILED, note=str(exc)[:200]))
            tally.asked += len(batch)
            continue

        answers = _answers_by_id(body, len(batch))
        tally.asked += len(batch)
        for i, item in enumerate(batch, 1):
            answer = answers.get(i)
            if answer is None:
                logger.debug("%s: no answer for row %d", adj.task, i)
                tally.count(Decision(FAILED, note="model returned no row"))
                continue
            problem = shape_problem(answer, adj.required)
            if problem is not None:
                # Recorded, not discarded: a malformed answer is a fact about
                # this prompt, and re-buying it next run would teach nothing.
                logger.debug("%s: row %d %s", adj.task, i, problem)
                decision = Decision(FAILED, note=problem)
                ledger.record(
                    Entry(
                        task=adj.task,
                        input_key=adj.key(item),
                        prompt_version=adj.prompt_version,
                        model=model_slug,
                        answer=answer,
                        outcome=decision.outcome,
                        note=problem,
                    )
                )
                tally.count(decision)
                continue
            decision = adj.decide(item, answer)
            ledger.record(
                Entry(
                    task=adj.task,
                    input_key=adj.key(item),
                    prompt_version=adj.prompt_version,
                    model=model_slug,
                    answer=answer,
                    outcome=decision.outcome,
                    note=decision.note,
                )
            )
            tally.count(decision)

        # Flushed per batch, not at the end. A queue is thousands of rows and
        # an hour of calls; holding the answers in memory until the run
        # finishes means an interruption throws away everything it paid for,
        # and the next run buys it all again.
        ledger.flush()

    ledger.flush()
    return tally
