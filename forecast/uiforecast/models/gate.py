"""The pre-registered gating test vehicle: AggADL.

AggADL = the AR-NSA baseline plus a small set of WARN terms. Both models
share the identical yoy-ratio AR core, so the WARN contribution is isolated.

WARN terms (yoy log1p deviations, consistent with the ratio-space AR):
- eff0: smoothed national effective workers, target week (the nowcast term:
  separations scheduled to land in the very week being nowcast)
- eff1: same, one week earlier (separation-to-filing smear)
- ann48: announced workers averaged over lags 4-8 (the longer-lead channel,
  collapsed to one term instead of a free kernel)

Gate decision logic (pre-registered in config/gate.yaml) lives in
`evaluate_gate`: pass criteria on full-sample and event-week RMSE ratios
with DM p-values, required to be sign-stable across lag quantiles.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from uiforecast.eval.dm import dm_test
from uiforecast.models.base import InfoSet
from uiforecast.models.baselines import ARNSA


def _yoy_log1p(s: pd.Series, weeks: pd.DatetimeIndex, tol_days: int = 10) -> pd.Series:
    """log1p(s_t) - log1p(s_{t-1y}) evaluated at each week in `weeks`."""
    idx = pd.DatetimeIndex(s.index)
    out = {}
    for ts in weeks:
        if ts not in idx:
            continue
        prior = ts - pd.DateOffset(years=1)
        diffs = np.abs((idx - prior).days)
        j = int(np.argmin(diffs))
        if diffs[j] <= tol_days:
            out[ts] = float(np.log1p(s.loc[ts]) - np.log1p(s.iloc[j]))
    return pd.Series(out)


@dataclass
class AggADL(ARNSA):
    name: str = "agg_adl"

    def _exog(self, info: InfoSet, weeks: pd.DatetimeIndex) -> pd.DataFrame | None:
        if info.warn_national is None:
            raise ValueError("agg_adl: no WARN aggregate in info set")
        w = info.warn_national
        eff = w["effective_sm"]
        ann = w["announced_sm"]
        ann48 = ann.rolling(5, min_periods=3).mean().shift(4)  # lags 4-8 average
        cols = {
            "eff0": _yoy_log1p(eff, weeks),
            "eff1": _yoy_log1p(eff.shift(1).dropna(), weeks),
            "ann48": _yoy_log1p(ann48.dropna(), weeks),
        }
        return pd.DataFrame(cols).reindex(weeks)


def evaluate_gate(
    results: pd.DataFrame,
    warn_model: str = "agg_adl",
    baseline: str = "ar_nsa",
    event_flow: pd.Series | None = None,
) -> dict:
    """Score the gate from a backtest results frame (one lag_q at a time).

    Criterion 1: full-sample (non-COVID) RMSE ratio < 0.99 and DM p < 0.10.
    Criterion 2: top-quintile event weeks (by as-of WARN flow) ratio < 0.95
                 and DM p < 0.10.
    """
    ok = results[results["error"].isna() & ~results["is_covid"]]
    piv_sq = ok.pivot_table(index="target_week", columns="model", values="sq_err")
    piv_sq = piv_sq.dropna(subset=[warn_model, baseline])

    def _stats(frame: pd.DataFrame) -> dict:
        rmse_w = float(np.sqrt(frame[warn_model].mean()))
        rmse_b = float(np.sqrt(frame[baseline].mean()))
        stat, p = dm_test(frame[warn_model].values, frame[baseline].values)
        return {
            "n": len(frame),
            "rmse_warn": rmse_w,
            "rmse_base": rmse_b,
            "ratio": rmse_w / rmse_b,
            "dm_p": p,
        }

    out = {"full": _stats(piv_sq)}
    if event_flow is not None:
        flow = event_flow.reindex(piv_sq.index).dropna()
        if len(flow) >= 25:
            cutoff = flow.quantile(0.8)
            ev = piv_sq.loc[flow[flow >= cutoff].index]
            out["event"] = _stats(ev)
    full_pass = out["full"]["ratio"] < 0.99 and out["full"]["dm_p"] < 0.10
    event_pass = (
        "event" in out
        and out["event"]["ratio"] < 0.95
        and out["event"]["dm_p"] < 0.10
    )
    out["pass_full"] = bool(full_pass)
    out["pass_event"] = bool(event_pass)
    out["pass"] = bool(full_pass or event_pass)
    return out
