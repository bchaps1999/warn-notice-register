"""Predictive density -> Kalshi-style bracket probabilities.

Until real Kalshi bracket definitions are ingested (Phase 4), synthetic
brackets mimic the market's usual structure: 10k-wide bands centered near
the model-free anchor (last week's SA print, rounded to 10k), with open
tails. The anchor uses only origin-time information -- no leakage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Bracket:
    lo: float  # inclusive; -inf for bottom tail
    hi: float  # exclusive; +inf for top tail

    def label(self) -> str:
        if np.isinf(self.lo):
            return f"<{self.hi / 1000:.0f}k"
        if np.isinf(self.hi):
            return f">={self.lo / 1000:.0f}k"
        return f"{self.lo / 1000:.0f}-{self.hi / 1000:.0f}k"


def synthetic_brackets(anchor_sa: float, width: float = 10_000, n_bands: int = 8) -> list[Bracket]:
    center = np.round(anchor_sa / width) * width
    half = n_bands // 2
    edges = [center + (i - half) * width for i in range(n_bands + 1)]
    brackets = [Bracket(-np.inf, edges[0])]
    brackets += [Bracket(edges[i], edges[i + 1]) for i in range(n_bands)]
    brackets.append(Bracket(edges[-1], np.inf))
    return brackets


def bracket_probs(sa_draws: np.ndarray, brackets: list[Bracket]) -> pd.Series:
    x = np.asarray(sa_draws)
    probs = {b.label(): float(np.mean((x >= b.lo) & (x < b.hi))) for b in brackets}
    return pd.Series(probs)


def realized_bracket(realized: float, brackets: list[Bracket]) -> str:
    for b in brackets:
        if b.lo <= realized < b.hi:
            return b.label()
    raise ValueError(f"{realized} not in any bracket")


def bracket_log_score(
    sa_draws: np.ndarray, realized: float, brackets: list[Bracket], floor: float = 1e-4
) -> float:
    """Negative log probability assigned to the realized bracket (lower better)."""
    probs = bracket_probs(sa_draws, brackets)
    p = probs[realized_bracket(realized, brackets)]
    return float(-np.log(max(p, floor)))
