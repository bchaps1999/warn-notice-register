"""Seasonal factor recovery and forecasting.

The tradeable number is SA; we model NSA and map through DOL's factor. For
backtests, the factor actually applied to each advance print is recovered
exactly from the advance prints themselves:

    multiplicative:  factor_mult = ICNSA_adv / ICSA_adv   (SA = NSA / f)
    additive:        factor_add  = ICNSA_adv - ICSA_adv   (SA = NSA - f)

Regime by release date (BLS/DOL, verified July 2026):
  - releases before 2020-09-03: multiplicative
  - releases 2020-09-03 .. 2023-04-05: additive (pandemic level-shift period)
  - releases from 2023-04-06 (annual revision): multiplicative again

For forecasting the *target* week's factor at an origin (before the print),
the honest live source is DOL's published projected factors; the backtest
approximation is the implied factor for the same week-of-year one year
earlier, with an error distribution estimated from historical one-year-ahead
factor deviations. That error is carried into the predictive density.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

ADDITIVE_START = date(2020, 9, 3)   # first additive release
ADDITIVE_END = date(2023, 4, 6)     # 2023 annual revision restored multiplicative


def regime_for_release(release_date: date) -> str:
    if ADDITIVE_START <= release_date < ADDITIVE_END:
        return "additive"
    return "multiplicative"


def implied_factors(
    icsa_adv: pd.DataFrame, icnsa_adv: pd.DataFrame
) -> pd.DataFrame:
    """Join ICSA/ICNSA advance prints; recover the implied factor per week.

    Inputs are `advance_prints()` frames (index week_ending; columns
    advance, release_date). Returns frame indexed by week_ending with:
    sa, nsa, release_date, regime, factor_mult, factor_add.
    """
    df = pd.DataFrame(
        {
            "sa": icsa_adv["advance"],
            "nsa": icnsa_adv["advance"],
            "release_date": icsa_adv["release_date"],
        }
    ).dropna(subset=["sa", "nsa"])
    df["regime"] = [
        regime_for_release(pd.Timestamp(r).date()) for r in df["release_date"]
    ]
    df["factor_mult"] = df["nsa"] / df["sa"]
    df["factor_add"] = df["nsa"] - df["sa"]
    return df


@dataclass(frozen=True)
class FactorForecast:
    """Factor applicable to a target week, with uncertainty."""

    regime: str
    center: float                 # point factor (mult ratio or add level)
    errors: np.ndarray            # empirical error draws around center

    def apply(self, nsa_draws: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Map NSA draws to SA draws, sampling factor error per draw."""
        f = self.center + rng.choice(self.errors, size=nsa_draws.shape)
        if self.regime == "multiplicative":
            f = np.clip(f, 0.05, None)
            return nsa_draws / f
        return nsa_draws - f


def factor_forecast(
    factors: pd.DataFrame,
    target_week: date,
    asof_release: date,
    min_history: int = 3,
) -> FactorForecast:
    """Forecast the factor for `target_week` using only releases < asof.

    Point = implied factor for the closest same-week-of-year observation one
    year earlier; errors = historical distribution of (realized factor -
    year-earlier factor) for the applicable regime, pooled across weeks.
    """
    known = factors[
        pd.to_datetime(factors["release_date"]).dt.date < asof_release
    ].copy()
    if len(known) < 60:
        raise ValueError("Not enough factor history before origin")
    regime = regime_for_release(asof_release)
    col = "factor_mult" if regime == "multiplicative" else "factor_add"

    idx = pd.to_datetime(known.index)
    target = pd.Timestamp(target_week)

    def _nearest(ts: pd.Timestamp) -> float | None:
        diffs = (idx - ts).days.values
        ok = np.abs(diffs) <= 10
        if not ok.any():
            return None
        j = np.argmin(np.abs(diffs) + (~ok) * 10_000)
        return float(known[col].iloc[j])

    center = _nearest(target - pd.DateOffset(years=1))
    if center is None:
        raise ValueError(f"No year-earlier factor near {target_week}")

    # error distribution: realized factor minus year-earlier factor, within
    # same-regime weeks only (mult errors are ratio-scale, add are levels)
    same = known[known["regime"] == regime]
    errs = []
    sidx = pd.to_datetime(same.index)
    svals = same[col].values
    for i, ts in enumerate(sidx):
        prior = ts - pd.DateOffset(years=1)
        diffs = np.abs((sidx - prior).days.values)
        j = int(np.argmin(diffs))
        if diffs[j] <= 10 and j != i:
            errs.append(float(svals[i] - svals[j]))
    if len(errs) < min_history:
        errs = [0.0]
    return FactorForecast(regime=regime, center=center, errors=np.array(errs))
