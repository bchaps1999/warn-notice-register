"""Date and headcount imputation for WARN notices.

Rules (per plan):
- announced date = notice_date; if missing, effective_date - 60d (statutory),
  tagged `announced_imputed`. States with NO real notice_dates (MI/NJ/PA/RI)
  are excluded from the announced series downstream via the imputed flag.
- effective date = effective_date; if missing, notice_date + 60d, tagged
  `effective_imputed`. Future-dated effective dates are legitimate scheduled
  separations -- never clipped.
- employees_affected NULL -> per-state median of non-null values (tagged);
  rows with neither date are dropped.
"""

from __future__ import annotations

import pandas as pd

STATUTORY_NOTICE_DAYS = 60


def impute_notices(notices: pd.DataFrame) -> pd.DataFrame:
    """Add announced_date / effective_date_final / workers columns + flags."""
    df = notices.copy()
    nd = pd.to_datetime(df["notice_date"], errors="coerce")
    ed = pd.to_datetime(df["effective_date"], errors="coerce")
    off = pd.Timedelta(days=STATUTORY_NOTICE_DAYS)

    df["announced_date"] = nd.where(nd.notna(), ed - off)
    df["announced_imputed"] = nd.isna() & ed.notna()
    df["effective_date_final"] = ed.where(ed.notna(), nd + off)
    df["effective_imputed"] = ed.isna() & nd.notna()
    df = df[df["announced_date"].notna()].copy()  # drops rows with no dates at all

    workers = pd.to_numeric(df["employees_affected"], errors="coerce")
    med = workers.groupby(df["state"]).transform("median")
    global_med = workers.median()
    df["workers"] = workers.fillna(med).fillna(global_med)
    df["workers_imputed"] = workers.isna()
    return df
