"""As-of reconstruction of the WARN notice information set.

Two inclusion paths per notice (plan §panel):
1. Real vintage: first_seen strictly after VINTAGE_EPOCH (the bulk-load date;
   everything loaded on/before it carries no vintage information) -> the
   notice is visible iff first_seen <= asof. Field values come from the
   latest notice_versions row with observed_at <= asof.
2. Fallback (pre-vintage history): visible iff
   notice_date + lag_model.days(state, q) <= asof. Notices with no
   notice_date use (effective_date - 60d) as the proxy filing date.

An optional pseudo-vintage table (mined from warn-github-flow git history,
keyed by source_notice_id) takes precedence over the fallback when present.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from uiforecast.panel.lags import LagModel

# every row bulk-loaded in the initial backfill carries this first_seen date;
# only strictly-later first_seen values are real vintages
DEFAULT_VINTAGE_EPOCH = date(2026, 7, 16)

NOTICE_COLS = [
    "id",
    "state",
    "employer_name",
    "notice_date",
    "effective_date",
    "employees_affected",
    "layoff_type",
    "is_temporary",
    "is_amended",
    "source_notice_id",
    "first_seen",
    "last_seen",
]


@dataclass
class AsOfStore:
    sqlite_path: Path
    lag_model: LagModel
    vintage_epoch: date = DEFAULT_VINTAGE_EPOCH
    pseudo_vintages: pd.DataFrame | None = None  # cols: source_notice_id, visible_date
    exclude_states: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        conn = sqlite3.connect(f"file:{self.sqlite_path}?mode=ro", uri=True)
        try:
            self._notices = pd.read_sql_query(
                f"SELECT {', '.join(NOTICE_COLS)} FROM notices", conn
            )
            self._versions = pd.read_sql_query(
                "SELECT notice_id, version, fields_json, observed_at "
                "FROM notice_versions ORDER BY notice_id, version",
                conn,
            )
        finally:
            conn.close()
        if self.exclude_states:
            self._notices = self._notices[
                ~self._notices["state"].isin(self.exclude_states)
            ]
        self._notices["first_seen_d"] = pd.to_datetime(
            self._notices["first_seen"]
        ).dt.date
        pv = self.pseudo_vintages
        if pv is not None:
            self._pv_map = pv.set_index("source_notice_id")["visible_date"]
        else:
            self._pv_map = None

    def notices_asof(self, asof: datetime | date, lag_q: float = 0.5) -> pd.DataFrame:
        """Notice-level frame as knowable at `asof`."""
        asof_d = asof.date() if isinstance(asof, datetime) else asof
        df = self._notices

        real = df["first_seen_d"] > self.vintage_epoch
        vis_real = real & (df["first_seen_d"] <= asof_d)

        # fallback visibility date: filing date + state lag quantile
        nd = pd.to_datetime(df["notice_date"], errors="coerce")
        ed = pd.to_datetime(df["effective_date"], errors="coerce")
        filing = nd.where(nd.notna(), ed - timedelta(days=60))
        lag_days = df["state"].map(
            lambda st: self.lag_model.days(st, lag_q)
        )
        visible_date = filing + pd.to_timedelta(lag_days, unit="D")

        if self._pv_map is not None:
            pv_dates = pd.to_datetime(
                df["source_notice_id"].map(self._pv_map), errors="coerce"
            )
            visible_date = pv_dates.where(pv_dates.notna(), visible_date)

        vis_fallback = (
            ~real
            & visible_date.notna()
            & (visible_date.dt.date <= asof_d)
        )
        out = df[vis_real | vis_fallback].drop(columns=["first_seen_d"]).copy()

        # amendments: roll back fields to the version knowable at asof
        amended_ids = out.loc[out["is_amended"] == 1, "id"]
        if len(amended_ids):
            vs = self._versions[self._versions["notice_id"].isin(amended_ids)]
            vs = vs[pd.to_datetime(vs["observed_at"]).dt.date <= asof_d]
            latest = vs.groupby("notice_id").tail(1)
            fields = {
                row.notice_id: json.loads(row.fields_json)
                for row in latest.itertuples()
            }
            for col in ("notice_date", "effective_date", "employees_affected",
                        "layoff_type", "is_temporary"):
                out[col] = out.apply(
                    lambda r, c=col: fields[r["id"]].get(c, r[c])
                    if r["id"] in fields
                    else r[c],
                    axis=1,
                )
            # amended notices whose *first* version postdates asof were
            # already excluded by first_seen; ones with no version <= asof
            # but visible via fallback keep current fields (best available)
        return out.reset_index(drop=True)
