"""Weekly state x series WARN panel construction.

Produces, per (week_ending_saturday, state, sector="ALL"):
- announced_workers / n_announced: workers whose notice was FILED that week
  (real notice_date only -- imputed announced dates are excluded so the
  announced series stays a genuine filing-time signal)
- effective_workers / n_effective: workers whose (possibly imputed)
  separation date falls in that week
- share_imputed_effective: worker-weighted share of imputed effective dates

Weeks with a reporting state and no notices are true zeros; weeks before a
state's first-ever observed notice are NaN (state not yet in panel).

Also provides the national aggregate used by the gate test: a sum over a
balanced state set (fixed over the evaluation window) to avoid level breaks
as states enter, plus smoothed transforms (trailing 4-week sum).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from uiforecast.calendar import claims_week
from uiforecast.panel.impute import impute_notices

SECTOR_ALL = "ALL"


def _week(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).map(lambda d: claims_week(d.date()) if pd.notna(d) else None)


def build_weekly_panel(
    notices: pd.DataFrame,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Long panel indexed by (week_ending, state) over [start, end] weeks."""
    df = impute_notices(notices)

    df["announced_week"] = _week(df["announced_date"])
    df["effective_week"] = _week(df["effective_date_final"])

    weeks = pd.to_datetime(
        pd.date_range(claims_week(start), claims_week(end), freq="W-SAT")
    )
    states = sorted(df["state"].unique())
    full_idx = pd.MultiIndex.from_product(
        [weeks, states], names=["week_ending", "state"]
    )

    ann = df[~df["announced_imputed"]].copy()
    ann_g = (
        ann.assign(week_ending=pd.to_datetime(ann["announced_week"]))
        .groupby(["week_ending", "state"])
        .agg(announced_workers=("workers", "sum"), n_announced=("workers", "size"))
    )
    eff = df.dropna(subset=["effective_week"]).copy()
    eff = eff.assign(
        week_ending=pd.to_datetime(eff["effective_week"]),
        imputed_workers=eff["workers"] * eff["effective_imputed"],
    )
    eff_g = eff.groupby(["week_ending", "state"]).agg(
        effective_workers=("workers", "sum"),
        n_effective=("workers", "size"),
        imputed_workers=("imputed_workers", "sum"),
    )

    panel = pd.DataFrame(index=full_idx).join(ann_g).join(eff_g)
    panel["share_imputed_effective"] = (
        panel["imputed_workers"] / panel["effective_workers"]
    ).fillna(0.0)
    panel = panel.drop(columns=["imputed_workers"])
    panel = panel.fillna(
        {"announced_workers": 0.0, "n_announced": 0, "effective_workers": 0.0,
         "n_effective": 0}
    )

    # NaN out weeks before each state's first-ever notice (not yet reporting)
    first_week = (
        df.groupby("state")[["announced_week", "effective_week"]]
        .min()
        .min(axis=1)
    )
    fw = panel.index.get_level_values("state").map(first_week)
    before_entry = pd.to_datetime(panel.index.get_level_values("week_ending")) < pd.to_datetime(fw)
    value_cols = ["announced_workers", "n_announced", "effective_workers", "n_effective"]
    panel.loc[before_entry, value_cols] = np.nan

    panel["sector"] = SECTOR_ALL
    return panel


def balanced_states(
    panel: pd.DataFrame,
    start: date,
    end: date,
    min_nonzero_share: float = 0.05,
    series: str = "announced_workers",
) -> list[str]:
    """States continuously reporting over [start, end]: no NaNs (entered the
    panel before `start`) and at least `min_nonzero_share` of weeks nonzero."""
    w = panel.reset_index()
    w = w[
        (w["week_ending"] >= pd.Timestamp(start))
        & (w["week_ending"] <= pd.Timestamp(end))
    ]
    out = []
    for st, grp in w.groupby("state"):
        vals = grp[series]
        if vals.isna().any():
            continue
        if (vals > 0).mean() >= min_nonzero_share:
            out.append(st)
    return sorted(out)


def national_aggregate(
    panel: pd.DataFrame,
    states: list[str],
    smooth_weeks: int = 4,
) -> pd.DataFrame:
    """National weekly series over a fixed (balanced) state set.

    Columns: announced, effective, announced_sm, effective_sm (trailing
    `smooth_weeks`-week mean), n_states.
    """
    sub = panel[panel.index.get_level_values("state").isin(states)]
    agg = sub.groupby(level="week_ending")[
        ["announced_workers", "effective_workers"]
    ].sum()
    agg.columns = ["announced", "effective"]
    agg["n_states"] = sub.groupby(level="week_ending")["announced_workers"].count()
    agg["announced_sm"] = agg["announced"].rolling(smooth_weeks, min_periods=1).mean()
    agg["effective_sm"] = agg["effective"].rolling(smooth_weeks, min_periods=1).mean()
    return agg
