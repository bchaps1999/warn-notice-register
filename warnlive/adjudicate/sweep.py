"""Comparing prompts and settings before one of them is trusted.

A threshold and a prompt are choices, and choosing them by looking at a
result and editing until it improves is fitting to that result. So the
labelled employers are split in half and never mixed: variants are compared
on the tune half, and the winner is scored once on the test half. What the
test half reports is the number worth quoting, because nothing was changed
in response to it.

The sweep is deliberately coarse. Three hundred employers separates a prompt
that misunderstands the task from one that does not; it does not separate
eighty per cent from eighty-three, and reading it as though it does is how a
sweep turns into noise-chasing. Configurations that survive are re-run at
size, which is the only comparison fine enough to choose between them.

Every run is keyed in the ledger by (task, row, prompt, model), so a
configuration already tried replays for nothing and the sweep can be widened
without re-buying what it already knows.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from warnlive.adjudicate import queue as queue_mod
from warnlive.adjudicate.client import Client, resolve
from warnlive.adjudicate.industry import Industry, score
from warnlive.adjudicate.ledger import Ledger

logger = logging.getLogger("warnlive")

RESULTS_PATH = Path("data/health/industry_sweep.csv")
FIELDS = [
    "prompt", "model", "batch_size", "thinking", "answered",
    "coverage", "precision", "worker_precision", "cost", "reasoning_tokens",
]


@dataclass
class Config:
    """One combination to try."""

    prompt: str
    model: str = "flash"
    provider: str | None = None
    batch_size: int = 20
    thinking: bool = True

    def label(self) -> str:
        bits = [self.prompt, self.model, f"b{self.batch_size}"]
        if not self.thinking:
            bits.append("no-think")
        return " ".join(bits)

    def version(self, default_batch: int) -> str:
        """The ledger version for this configuration.

        Settings that change the answer belong in the key, not just the
        prompt name. Batch size and thinking both do — a batch of ten is a
        different question from a batch of twenty, and reasoning is most of
        how the answer is reached. Left out, two configurations would write
        under one key: the second would find the first's answers already
        there, skip every row, and be scored on them. That is the same
        mistake as reusing a prompt name after editing it, one level down.

        The model is not encoded here because the ledger keys it separately.
        """
        bits = [self.prompt]
        if self.batch_size != default_batch:
            bits.append(f"b{self.batch_size}")
        if not self.thinking:
            bits.append("nothink")
        return "+".join(bits)


@dataclass
class Result:
    config: Config
    #: employers the model gave a sector for, at any confidence. Lower than
    #: the sample when a prompt abstains, which is itself worth seeing.
    answered: int = 0
    curve: list[dict] = field(default_factory=list)
    cost: float = 0.0
    reasoning: int = 0

    def at(self, cut: float) -> dict | None:
        """The curve row for a confidence cut, if the run reached it."""
        return next((r for r in self.curve if abs(r["threshold"] - cut) < 1e-9), None)


def run(
    items: list[dict],
    configs: list[Config],
    cut: float = 0.9,
    budget_each: float | None = None,
    ledger: Ledger | None = None,
) -> list[Result]:
    """Grade every configuration over the same employers, and tabulate.

    The same employers for all of them, so a difference between two rows is
    the configuration and not the draw.
    """
    ledger = ledger if ledger is not None else Ledger()
    results = []
    for config in configs:
        model = resolve(config.provider, config.model)
        client = Client(model, budget=budget_each)
        worker = Industry(prompt=config.prompt)
        default_batch = worker.batch_size
        worker.batch_size = config.batch_size
        worker.thinking = config.thinking
        # The prompt text is unchanged; what varies is how it was asked, and
        # that has to be in the key or two configurations share one.
        worker.prompt_version = config.version(default_batch)

        # Replay if this exact configuration has run before; otherwise insist
        # on asking. Without the second half, a second model under the same
        # prompt finds the first model's answers, skips every row, and is
        # scored on nothing — which is how the pro run once finished in
        # forty-seven milliseconds having called no API at all.
        seen = ledger.has_any(worker.task, worker.prompt_version, str(model))
        logger.info("sweep: %s%s", config.label(), "" if seen else " (asking)")
        queue_mod.run(
            worker, items, client=client, ledger=ledger, model=str(model),
            reask=not seen,
        )
        try:
            curve = score(items, worker, ledger, str(model))
        except RuntimeError as exc:
            logger.warning("sweep: %s graded nothing: %s", config.label(), exc)
            continue
        top = next((r for r in curve if abs(r["threshold"]) < 1e-9), None)
        results.append(Result(
            config=config,
            answered=top["answered"] if top else 0,
            curve=curve,
            cost=client.usage.cost,
            reasoning=client.usage.reasoning,
        ))
    return results


def write(results: list[Result], cut: float = 0.9,
          path: Path = RESULTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        for r in results:
            row = r.at(cut) or {}
            writer.writerow({
                "prompt": r.config.prompt, "model": r.config.model,
                "batch_size": r.config.batch_size,
                "thinking": int(r.config.thinking), "answered": r.answered,
                "coverage": row.get("coverage", ""),
                "precision": row.get("precision", ""),
                "worker_precision": row.get("worker_precision", ""),
                "cost": round(r.cost, 5), "reasoning_tokens": r.reasoning,
            })


def table(results: list[Result], cut: float = 0.9) -> str:
    """The comparison, best precision first."""
    lines = [
        f"  {'configuration':30}{'answered':>9}{'coverage':>10}"
        f"{'precision':>11}{'worker-prec':>13}{'cost':>9}"
    ]
    ranked = sorted(
        results, key=lambda r: -((r.at(cut) or {}).get("precision") or 0)
    )
    for r in ranked:
        row = r.at(cut)
        if not row:
            lines.append(
                f"  {r.config.label():30}{r.answered:>9}   (never reached {cut})"
            )
            continue
        lines.append(
            f"  {r.config.label():30}{r.answered:>9}{row['coverage']:>9.1%}"
            f"{row['precision']:>11.1%}{row['worker_precision']:>13.1%}"
            f"{r.cost:>9.4f}"
        )
    return "\n".join(lines)
