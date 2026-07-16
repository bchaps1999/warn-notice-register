"""ALFRED vintage ingestion for ICSA / ICNSA.

Data access (verified): `alfredgraph.csv` serves one vintage per request with
no API key. Requesting `vintage_date=D` returns the latest vintage <= D; the
actual vintage date is embedded in the value column header (e.g.
`ICSA_20240606`). We request one vintage per calendar week (each Friday),
dedupe on the actual vintage date, and cache every raw response on disk so
re-runs are offline.

The advance ("first release") print for claims week w is the value of the
maximum observation date in the vintage released the following Thursday --
recovered here as: the first vintage in which observation w appears.

If FRED_API_KEY is set, the official API could pull the full vintage matrix
in a few calls; not implemented until a key exists.
"""

from __future__ import annotations

import hashlib
import io
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
USER_AGENT = "warn-live-uiforecast/0.1 (research; contact repo owner)"


def _cache_path(cache_dir: Path, series_id: str, asof: date) -> Path:
    key = hashlib.sha1(f"{series_id}|{asof.isoformat()}".encode()).hexdigest()[:16]
    return cache_dir / f"{series_id}_{asof.isoformat()}_{key}.csv"


def fetch_vintage_csv(
    series_id: str,
    asof: date,
    cache_dir: Path,
    session: requests.Session | None = None,
    sleep_s: float = 0.35,
    timeout: int = 60,
) -> str:
    """Raw CSV text for the latest vintage of series_id as of `asof` (cached)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, series_id, asof)
    if path.exists():
        return path.read_text()
    sess = session or requests.Session()
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            resp = sess.get(
                BASE_URL,
                params={"id": series_id, "vintage_date": asof.isoformat()},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            resp.raise_for_status()
            text = resp.text
            break
        except (requests.RequestException, OSError) as err:  # retry transient
            last_err = err
            time.sleep(5 * (attempt + 1))
    else:
        raise RuntimeError(f"ALFRED fetch failed for {series_id}@{asof}") from last_err
    if not text.startswith("observation_date"):
        raise ValueError(
            f"Unexpected ALFRED response for {series_id}@{asof}: {text[:200]!r}"
        )
    path.write_text(text)
    time.sleep(sleep_s)
    return text


def parse_vintage(csv_text: str) -> tuple[date, pd.Series]:
    """Parse alfredgraph CSV -> (actual_vintage_date, series indexed by obs date)."""
    df = pd.read_csv(io.StringIO(csv_text), na_values=["."])
    value_col = [c for c in df.columns if c != "observation_date"]
    if len(value_col) != 1:
        raise ValueError(f"Expected one value column, got {df.columns.tolist()}")
    col = value_col[0]
    vintage = datetime.strptime(col.rsplit("_", 1)[1], "%Y%m%d").date()
    s = pd.Series(
        df[col].values,
        index=pd.to_datetime(df["observation_date"]).dt.date,
        name=col.rsplit("_", 1)[0],
    ).dropna()
    return vintage, s.astype(float)


def build_vintage_long(
    series_id: str,
    start: date,
    end: date,
    cache_dir: Path,
    progress: bool = False,
) -> pd.DataFrame:
    """Long vintage frame: columns (vintage, obs_date, value).

    One request per calendar week: we ask for the vintage as of each Friday
    (releases are Thu, occasionally Wed), then dedupe on actual vintage date.
    """
    session = requests.Session()
    seen: set[date] = set()
    frames: list[pd.DataFrame] = []
    # first Friday on/after start
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    n = 0
    while d <= end:
        text = fetch_vintage_csv(series_id, d, cache_dir, session=session)
        vintage, s = parse_vintage(text)
        if vintage not in seen:
            seen.add(vintage)
            frames.append(
                pd.DataFrame(
                    {"vintage": vintage, "obs_date": s.index, "value": s.values}
                )
            )
        d += timedelta(weeks=1)
        n += 1
        if progress and n % 26 == 0:
            print(f"  {series_id}: fetched through {d} ({len(seen)} vintages)")
    if not frames:
        raise ValueError(f"No vintages fetched for {series_id} in [{start}, {end}]")
    out = pd.concat(frames, ignore_index=True)
    out["obs_date"] = pd.to_datetime(out["obs_date"])
    out["vintage"] = pd.to_datetime(out["vintage"])
    return out.sort_values(["vintage", "obs_date"]).reset_index(drop=True)


def advance_prints(long_df: pd.DataFrame) -> pd.DataFrame:
    """First-release value per observation week.

    Returns frame indexed by week_ending (obs_date) with columns:
    advance (value in the earliest vintage containing that obs) and
    release_date (that vintage's date = actual release day).

    Only observations first appearing within the fetched vintage range are
    'true' advance prints; obs older than the earliest vintage are dropped.
    """
    first_vintage = long_df["vintage"].min()
    idx = long_df.groupby("obs_date")["vintage"].idxmin()
    adv = long_df.loc[idx, ["obs_date", "vintage", "value"]].set_index("obs_date")
    adv = adv.rename(columns={"vintage": "release_date", "value": "advance"})
    # an obs is a genuine first release only if it *entered* after the first
    # fetched vintage (obs already present in the earliest vintage are history)
    earliest_obs_in_first = long_df.loc[
        long_df["vintage"] == first_vintage, "obs_date"
    ].max()
    adv = adv[adv.index > earliest_obs_in_first]
    return adv.sort_index()


def history_asof(long_df: pd.DataFrame, asof: datetime | date) -> pd.Series:
    """Full series as known at `asof` (latest vintage <= asof)."""
    asof_ts = pd.Timestamp(asof)
    vintages = long_df["vintage"].unique()
    valid = [v for v in vintages if pd.Timestamp(v) <= asof_ts]
    if not valid:
        raise ValueError(f"No vintage at or before {asof}")
    v = max(valid)
    sub = long_df[long_df["vintage"] == v]
    return pd.Series(sub["value"].values, index=sub["obs_date"].values).sort_index()
