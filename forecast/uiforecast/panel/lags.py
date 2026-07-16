"""Per-state WARN reporting-lag model.

"Reporting lag" = days between a notice's filing date (notice_date) and the
date it became observable to us (first_seen). Where real vintage history
exists (first_seen after the bulk-load epoch), the lag CDF is estimated
empirically per state; before that, configured fallback quantiles apply.

The lag quantile is the key sensitivity knob for backtest honesty: every
result must be reported at q=0.1 / 0.5 / 0.9 (a higher quantile assumes
notices became visible LATER, i.e. is more conservative about what the
model could have known).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# conservative defaults: portals typically post within days; batch publishers
# can take weeks. Overridable per state in config/state_lags.yaml.
DEFAULT_QUANTILES: dict[float, int] = {0.1: 3, 0.5: 7, 0.9: 21}


@dataclass
class LagModel:
    state_quantiles: dict[str, dict[float, int]] = field(default_factory=dict)
    default_quantiles: dict[float, int] = field(default_factory=lambda: dict(DEFAULT_QUANTILES))

    def days(self, state: str, q: float) -> int:
        table = self.state_quantiles.get(state, self.default_quantiles)
        if q in table:
            return int(table[q])
        qs = sorted(table)
        vals = [table[k] for k in qs]
        return int(round(float(np.interp(q, qs, vals))))

    @classmethod
    def from_yaml(cls, path: Path) -> "LagModel":
        raw = yaml.safe_load(path.read_text()) or {}
        default = {float(k): int(v) for k, v in raw.get("default", DEFAULT_QUANTILES).items()}
        states = {
            st: {float(k): int(v) for k, v in tbl.items()}
            for st, tbl in raw.get("states", {}).items()
        }
        return cls(state_quantiles=states, default_quantiles=default)

    @classmethod
    def from_observed(
        cls,
        notices: pd.DataFrame,
        vintage_epoch: date,
        quantiles: tuple[float, ...] = (0.1, 0.5, 0.9),
        min_obs: int = 20,
    ) -> "LagModel":
        """Empirical per-state lag quantiles from real vintage history.

        Uses rows with first_seen strictly after the bulk-load epoch and a
        non-null notice_date. States with too few observations fall back to
        the pooled cross-state distribution (or defaults if nothing pooled).
        """
        df = notices.copy()
        df["first_seen_d"] = pd.to_datetime(df["first_seen"]).dt.date
        df = df[
            (df["first_seen_d"] > vintage_epoch) & df["notice_date"].notna()
        ].copy()
        df["lag"] = (
            pd.to_datetime(df["first_seen"]) - pd.to_datetime(df["notice_date"])
        ).dt.days.clip(lower=0)

        default = dict(DEFAULT_QUANTILES)
        if len(df) >= min_obs:
            default = {
                q: int(np.quantile(df["lag"], q)) for q in quantiles
            }
        states = {}
        for st, grp in df.groupby("state"):
            if len(grp) >= min_obs:
                states[st] = {q: int(np.quantile(grp["lag"], q)) for q in quantiles}
        return cls(state_quantiles=states, default_quantiles=default)
