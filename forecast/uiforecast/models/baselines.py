"""Baseline models the WARN model must beat.

All NSA-space models produce NSA draws (point x bootstrapped log residuals of
their own historical 1-step rule) and map to SA through the origin's
FactorForecast, which injects factor uncertainty. RandomWalkSA works directly
in SA space (the strongest naive competitor historically).

COVID weeks are excluded from residual pools via `covid_mask`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from uiforecast.models.base import InfoSet, PredictiveDensity

N_DRAWS = 20_000
COVID_START = pd.Timestamp("2020-03-07")
COVID_END = pd.Timestamp("2021-06-30")


def non_covid(idx: pd.DatetimeIndex) -> np.ndarray:
    return ~((idx >= COVID_START) & (idx <= COVID_END))


def _year_ago_value(s: pd.Series, ts: pd.Timestamp, tol_days: int = 10) -> float | None:
    idx = pd.DatetimeIndex(s.index)
    prior = ts - pd.DateOffset(years=1)
    diffs = np.abs((idx - prior).days)
    j = int(np.argmin(diffs))
    if diffs[j] <= tol_days:
        return float(s.iloc[j])
    return None


def _bootstrap_density(
    point_nsa: float,
    log_resids: np.ndarray,
    info: InfoSet,
    rng: np.random.Generator,
) -> PredictiveDensity:
    if len(log_resids) < 10:
        raise ValueError("Too few residuals for density")
    draws_nsa = point_nsa * np.exp(rng.choice(log_resids, size=N_DRAWS))
    sa = info.factor_fc.apply(draws_nsa, rng)
    sa = np.round(sa / 1000.0) * 1000.0
    return PredictiveDensity(sa_draws=sa, nsa_point=point_nsa)


@dataclass
class SeasonalNaiveNSA:
    """NSA point = same week last year x trailing 8-week level ratio."""

    name: str = "seasonal_naive"
    seed: int = 0
    _density: PredictiveDensity | None = field(default=None, repr=False)

    def _rule_point(self, s: pd.Series, ts: pd.Timestamp) -> float | None:
        base = _year_ago_value(s, ts)
        if base is None:
            return None
        hist = s[s.index < ts]
        recent = hist.tail(8)
        prior = s[
            (s.index >= ts - pd.DateOffset(years=1) - pd.Timedelta(weeks=8))
            & (s.index < ts - pd.DateOffset(years=1))
        ]
        if len(recent) < 4 or len(prior) < 4:
            return None
        return base * float(recent.mean() / prior.mean())

    def fit(self, info: InfoSet) -> None:
        rng = np.random.default_rng(self.seed + info.target_week.toordinal())
        s = info.nsa_history.copy()
        s.index = pd.DatetimeIndex(s.index)
        point = self._rule_point(s, info.target_week)
        if point is None:
            raise ValueError("seasonal naive: insufficient history")
        # historical 1-step log errors of the same rule
        resids = []
        eval_weeks = s.index[(s.index >= s.index[0] + pd.DateOffset(years=1, weeks=8))]
        for ts in eval_weeks:
            p = self._rule_point(s[s.index < ts], ts)
            if p is not None and p > 0:
                resids.append(np.log(float(s.loc[ts]) / p))
        resid_idx = eval_weeks[-len(resids):] if resids else eval_weeks[:0]
        resids = np.array(resids)[non_covid(resid_idx)]
        self._density = _bootstrap_density(point, resids, info, rng)

    def predict(self) -> PredictiveDensity:
        assert self._density is not None
        return self._density


@dataclass
class ARNSA:
    """AR(1) on the log year-over-year ratio of NSA claims.

    r_t = log(NSA_t) - log(NSA_{t-52w});  r_t = c + phi * r_{t-1} + eps.
    Handles seasonality without 52 dummies; COVID weeks dropped from the
    estimation sample. Subclasses add exogenous regressors via `_exog`.
    """

    name: str = "ar_nsa"
    seed: int = 0
    _density: PredictiveDensity | None = field(default=None, repr=False)

    def _exog(self, info: InfoSet, weeks: pd.DatetimeIndex) -> pd.DataFrame | None:
        return None  # baseline: no exogenous terms

    def fit(self, info: InfoSet) -> None:
        rng = np.random.default_rng(self.seed + info.target_week.toordinal())
        s = info.nsa_history.copy()
        s.index = pd.DatetimeIndex(s.index)
        log_s = np.log(s.astype(float))
        r = pd.Series(
            {
                ts: log_s.loc[ts] - ya
                for ts in log_s.index
                if (ya := _log_year_ago(log_s, ts)) is not None
            }
        ).sort_index()
        df = pd.DataFrame({"r": r, "r_lag": r.shift(1)}).dropna()
        exog = self._exog(info, pd.DatetimeIndex(df.index))
        if exog is not None:
            df = df.join(exog).dropna()
        keep = non_covid(pd.DatetimeIndex(df.index))
        train = df[keep]
        if len(train) < 60:
            raise ValueError("ar_nsa: insufficient history")
        X = np.column_stack(
            [np.ones(len(train)), train.drop(columns=["r"]).values]
        )
        y = train["r"].values
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta

        # one-step forecast for the target week
        r_last = float(r.loc[r.index < info.target_week].iloc[-1])
        x_row = [1.0, r_last]
        if exog is not None:
            x_target = self._exog(info, pd.DatetimeIndex([info.target_week]))
            x_row.extend(x_target.iloc[0].values)
        r_hat = float(np.array(x_row) @ beta)
        ya = _log_year_ago(log_s, info.target_week)
        if ya is None:
            raise ValueError("ar_nsa: no year-ago value for target week")
        point = float(np.exp(ya + r_hat))
        self._density = _bootstrap_density(point, resid, info, rng)

    def predict(self) -> PredictiveDensity:
        assert self._density is not None
        return self._density


def _log_year_ago(log_s: pd.Series, ts: pd.Timestamp) -> float | None:
    v = _year_ago_value(np.exp(log_s), ts)
    return float(np.log(v)) if v is not None and v > 0 else None


@dataclass
class RandomWalkSA:
    """SA draws = last SA print + bootstrapped weekly SA changes."""

    name: str = "rw_sa"
    seed: int = 0
    _density: PredictiveDensity | None = field(default=None, repr=False)

    def fit(self, info: InfoSet) -> None:
        rng = np.random.default_rng(self.seed + info.target_week.toordinal())
        s = info.sa_history.copy()
        s.index = pd.DatetimeIndex(s.index)
        s = s[s.index < info.target_week]
        if len(s) < 60:
            raise ValueError("rw_sa: insufficient history")
        changes = s.diff().dropna()
        changes = changes[non_covid(pd.DatetimeIndex(changes.index))].values
        last = float(s.iloc[-1])
        draws = last + rng.choice(changes, size=N_DRAWS)
        draws = np.round(np.clip(draws, 1000, None) / 1000.0) * 1000.0
        self._density = PredictiveDensity(sa_draws=draws, nsa_point=None)

    def predict(self) -> PredictiveDensity:
        assert self._density is not None
        return self._density
