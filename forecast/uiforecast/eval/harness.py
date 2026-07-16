"""Rolling-origin backtest driver.

For each weekly origin (Wednesday 23:59 ET before the Thursday release), an
InfoSet is built strictly from origin-time information:
- ICNSA/ICSA histories = the latest ALFRED vintage dated <= origin
- implied factors from releases < origin; factor forecast for the target week
- WARN national aggregate from notices visible at the origin (as-of store)

Each model fits and emits a predictive density; forecasts and scores are
persisted to forecast.sqlite.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from uiforecast.calendar import canonical_origin, weekly_saturdays
from uiforecast.eval import brackets as br
from uiforecast.eval.metrics import crps_sample, log_score_kde, pit
from uiforecast.ingest import seasonal
from uiforecast.models.base import InfoSet
from uiforecast.models.baselines import COVID_END, COVID_START
from uiforecast.panel.asof import AsOfStore
from uiforecast.panel.impute import impute_notices
from uiforecast.panel.weekly import balanced_states, build_weekly_panel, national_aggregate


class WarnAsOfAggregator:
    """Fast as-of national WARN aggregates: precomputes per-notice visibility
    and event weeks once, then filters + groups per origin."""

    def __init__(
        self,
        store: AsOfStore,
        lag_q: float,
        panel_start: date,
        balanced_window: tuple[date, date],
    ):
        self.lag_q = lag_q
        self.panel_start = panel_start
        # visibility via the store's logic, applied once at a far-future date
        # to get the full notice set, then recompute visibility dates
        all_notices = store.notices_asof(date(2100, 1, 1), lag_q=lag_q)
        df = impute_notices(all_notices)

        first_seen_d = pd.to_datetime(df["first_seen"]).dt.date
        real = first_seen_d > store.vintage_epoch
        lag_days = df["state"].map(lambda st: store.lag_model.days(st, lag_q))
        fallback_visible = df["announced_date"] + pd.to_timedelta(lag_days, unit="D")
        df["visible"] = np.where(
            real, pd.to_datetime(df["first_seen"]), fallback_visible
        )
        df["visible"] = pd.to_datetime(df["visible"])
        self.df = df

        # balanced state set fixed over the evaluation window (full panel)
        panel = build_weekly_panel(df, panel_start, balanced_window[1])
        self.states = balanced_states(panel, *balanced_window)

    def national(self, origin: datetime, end_week: date) -> pd.DataFrame:
        vis = self.df[self.df["visible"] <= pd.Timestamp(origin)]
        vis = vis[vis["state"].isin(self.states)]
        panel = build_weekly_panel(vis, self.panel_start, end_week)
        return national_aggregate(panel, self.states)


@dataclass
class BacktestConfig:
    origin_start: date
    origin_end: date
    lag_q: float = 0.5
    seed: int = 0
    with_warn: bool = True
    panel_start: date = date(2013, 1, 1)
    db_path: Path = Path("data/runs/forecast.sqlite")
    run_id: str = "dev"
    model_factories: list = field(default_factory=list)  # callables -> model


def build_infoset(
    target_week: pd.Timestamp,
    origin: datetime,
    icsa_long: pd.DataFrame,
    icnsa_long: pd.DataFrame,
    factors: pd.DataFrame,
    warn_agg: WarnAsOfAggregator | None,
) -> InfoSet:
    from uiforecast.ingest.alfred import history_asof

    nsa = history_asof(icnsa_long, origin)
    sa = history_asof(icsa_long, origin)
    factor_fc = seasonal.factor_forecast(
        factors, target_week.date(), asof_release=origin.date()
    )
    warn = None
    if warn_agg is not None:
        warn = warn_agg.national(origin, target_week.date())
    return InfoSet(
        origin=origin,
        target_week=target_week,
        nsa_history=nsa,
        sa_history=sa,
        factors=factors[pd.to_datetime(factors["release_date"]) < pd.Timestamp(origin)],
        factor_fc=factor_fc,
        warn_national=warn,
    )


def run_backtest(
    cfg: BacktestConfig,
    icsa_long: pd.DataFrame,
    icnsa_long: pd.DataFrame,
    factors: pd.DataFrame,
    targets: pd.DataFrame,
    warn_agg: WarnAsOfAggregator | None,
    progress: bool = True,
) -> pd.DataFrame:
    rows = []
    target_weeks = [
        pd.Timestamp(w) for w in weekly_saturdays(cfg.origin_start, cfg.origin_end)
    ]
    for i, tw in enumerate(target_weeks):
        if tw not in targets.index:
            continue
        realized = float(targets.loc[tw, "sa_advance"])
        origin = canonical_origin(tw.date())
        try:
            info = build_infoset(
                tw, origin, icsa_long, icnsa_long, factors, warn_agg if cfg.with_warn else None
            )
        except ValueError:
            continue
        anchor = float(info.sa_history.iloc[-1])
        bracket_set = br.synthetic_brackets(anchor)
        is_covid = COVID_START <= tw <= COVID_END
        warn_eff_sm = None
        if info.warn_national is not None and tw in info.warn_national.index:
            warn_eff_sm = float(info.warn_national.loc[tw, "effective_sm"])
        for factory in cfg.model_factories:
            model = factory()
            try:
                model.fit(info)
                dens = model.predict()
            except ValueError as err:
                rows.append(
                    {"origin": origin, "target_week": tw, "model": model.name,
                     "error": str(err)}
                )
                continue
            rows.append(
                {
                    "origin": origin,
                    "target_week": tw,
                    "model": model.name,
                    "mean": dens.mean,
                    "q05": dens.quantile(0.05),
                    "q25": dens.quantile(0.25),
                    "q50": dens.quantile(0.50),
                    "q75": dens.quantile(0.75),
                    "q95": dens.quantile(0.95),
                    "realized_sa": realized,
                    "sq_err": (dens.mean - realized) ** 2,
                    "abs_err": abs(dens.mean - realized),
                    "crps": crps_sample(dens.sa_draws, realized),
                    "log_score": log_score_kde(dens.sa_draws, realized),
                    "pit": pit(dens.sa_draws, realized),
                    "bracket_ls": br.bracket_log_score(
                        dens.sa_draws, realized, bracket_set
                    ),
                    "is_covid": is_covid,
                    "warn_eff_sm": warn_eff_sm,
                    "error": None,
                }
            )
        if progress and (i + 1) % 52 == 0:
            print(f"  backtest: {i + 1}/{len(target_weeks)} origins")
    results = pd.DataFrame(rows)
    _persist(cfg, results)
    return results


def _persist(cfg: BacktestConfig, results: pd.DataFrame) -> None:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cfg.db_path)
    try:
        out = results.copy()
        out.insert(0, "run_id", cfg.run_id)
        out.insert(1, "lag_q", cfg.lag_q)
        for col in ("origin", "target_week"):
            out[col] = out[col].astype(str)
        out.to_sql("forecasts", conn, if_exists="append", index=False)
    finally:
        conn.close()
