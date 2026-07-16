from datetime import date

import pandas as pd

from uiforecast.ingest.alfred import advance_prints, history_asof, parse_vintage
from uiforecast.ingest.seasonal import regime_for_release

CSV = """observation_date,ICSA_20240606
2024-05-04,232000
2024-05-11,223000
2024-05-18,216000
2024-05-25,221000
2024-06-01,229000
"""


def test_parse_vintage():
    vintage, s = parse_vintage(CSV)
    assert vintage == date(2024, 6, 6)
    assert s.name == "ICSA"
    assert s.loc[date(2024, 6, 1)] == 229000.0
    assert len(s) == 5


def _long_fixture():
    # three weekly vintages; each adds one obs and revises the prior week
    rows = []
    obs = {
        "2024-06-06": {"2024-05-25": 221, "2024-06-01": 229},
        "2024-06-13": {"2024-05-25": 221, "2024-06-01": 231, "2024-06-08": 242},
        "2024-06-20": {"2024-05-25": 221, "2024-06-01": 231, "2024-06-08": 239,
                        "2024-06-15": 238},
    }
    for v, d in obs.items():
        for o, val in d.items():
            rows.append({"vintage": pd.Timestamp(v), "obs_date": pd.Timestamp(o),
                         "value": float(val)})
    return pd.DataFrame(rows)


def test_advance_prints_take_first_release():
    adv = advance_prints(_long_fixture())
    # 2024-06-01 was in the earliest vintage's history -> only newer obs count
    assert pd.Timestamp("2024-06-01") not in adv.index
    assert adv.loc[pd.Timestamp("2024-06-08"), "advance"] == 242.0  # not revised 239
    assert adv.loc[pd.Timestamp("2024-06-15"), "advance"] == 238.0
    assert adv.loc[pd.Timestamp("2024-06-08"), "release_date"] == pd.Timestamp(
        "2024-06-13"
    )


def test_history_asof_uses_latest_vintage_at_or_before():
    long_df = _long_fixture()
    h = history_asof(long_df, pd.Timestamp("2024-06-14"))
    assert h.loc[pd.Timestamp("2024-06-01")] == 231.0  # revised value known
    assert pd.Timestamp("2024-06-15") not in h.index   # future obs absent


def test_regimes():
    assert regime_for_release(date(2019, 6, 6)) == "multiplicative"
    assert regime_for_release(date(2020, 9, 3)) == "additive"
    assert regime_for_release(date(2021, 6, 10)) == "additive"
    assert regime_for_release(date(2023, 4, 6)) == "multiplicative"
    assert regime_for_release(date(2026, 7, 9)) == "multiplicative"
