import json
import sqlite3
from datetime import date, datetime

import pandas as pd
import pytest

from uiforecast.panel.asof import AsOfStore
from uiforecast.panel.impute import impute_notices
from uiforecast.panel.lags import LagModel
from uiforecast.panel.weekly import balanced_states, build_weekly_panel, national_aggregate

EPOCH = date(2026, 7, 16)


@pytest.fixture
def fixture_db(tmp_path):
    db = tmp_path / "warn.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE notices (
            id INTEGER PRIMARY KEY, dedupe_key TEXT, state TEXT,
            employer_name TEXT, location TEXT, notice_date TEXT,
            effective_date TEXT, employees_affected INTEGER,
            layoff_type TEXT, is_temporary INTEGER, is_amendment INTEGER,
            source_url TEXT, source_notice_id TEXT, is_amended INTEGER DEFAULT 0,
            current_version INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT
        );
        CREATE TABLE notice_versions (
            id INTEGER PRIMARY KEY, notice_id INTEGER, version INTEGER,
            raw_record_hash TEXT, fields_json TEXT, observed_at TEXT
        );
        """
    )
    rows = [
        # bulk-loaded history (fallback path): filed 2025-06-02, CA
        (1, "k1", "CA", "Acme", "LA", "2025-06-02", "2025-08-01", 100, None, None,
         0, None, "h1", 0, 1, "2026-07-16", "2026-07-16"),
        # bulk-loaded, no notice_date (NJ-style): effective only
        (2, "k2", "NJ", "Beta", "Newark", None, "2025-09-15", 50, None, None,
         0, None, "h2", 0, 1, "2026-07-16", "2026-07-16"),
        # real vintage: first seen 2026-08-03 (after epoch)
        (3, "k3", "CA", "Gamma", "SF", "2026-07-28", "2026-09-30", 200, None, None,
         0, None, "h3", 0, 1, "2026-08-03", "2026-08-03"),
        # amended notice: v1 said 80 workers (2026-08-01), v2 said 150 (2026-08-20)
        (4, "k4", "TX", "Delta", "Austin", "2026-07-25", "2026-09-20", 150, None,
         None, 0, None, "h4", 1, 2, "2026-08-01", "2026-08-20"),
    ]
    conn.executemany(
        "INSERT INTO notices VALUES (" + ",".join("?" * 17) + ")", rows
    )
    v1 = dict(state="TX", employer_name="Delta", notice_date="2026-07-25",
              effective_date="2026-09-20", employees_affected=80)
    v2 = dict(v1, employees_affected=150)
    conn.executemany(
        "INSERT INTO notice_versions VALUES (?,?,?,?,?,?)",
        [
            (1, 4, 1, "r1", json.dumps(v1), "2026-08-01"),
            (2, 4, 2, "r2", json.dumps(v2), "2026-08-20"),
        ],
    )
    conn.commit()
    conn.close()
    return db


def _store(db):
    lm = LagModel(state_quantiles={}, default_quantiles={0.1: 3, 0.5: 7, 0.9: 21})
    return AsOfStore(sqlite_path=db, lag_model=lm, vintage_epoch=EPOCH)


def test_fallback_visibility_respects_lag(fixture_db):
    store = _store(fixture_db)
    # notice 1 filed 2025-06-02; q=0.5 lag 7d -> visible 2025-06-09
    assert 1 not in store.notices_asof(date(2025, 6, 5))["id"].values
    assert 1 in store.notices_asof(date(2025, 6, 9))["id"].values
    # q=0.9 lag 21d -> not visible until 2025-06-23
    assert 1 not in store.notices_asof(date(2025, 6, 20), lag_q=0.9)["id"].values


def test_fallback_uses_effective_minus_60_when_no_notice_date(fixture_db):
    store = _store(fixture_db)
    # notice 2: proxy filing = 2025-09-15 - 60d = 2025-07-17; +7d lag = 2025-07-24
    assert 2 not in store.notices_asof(date(2025, 7, 20))["id"].values
    assert 2 in store.notices_asof(date(2025, 7, 24))["id"].values


def test_real_vintage_uses_first_seen(fixture_db):
    store = _store(fixture_db)
    # notice 3 filed 2026-07-28 but first_seen 2026-08-03: filing+lag would say
    # visible 2026-08-04, but the REAL vintage (first_seen) governs
    assert 3 not in store.notices_asof(date(2026, 8, 2))["id"].values
    assert 3 in store.notices_asof(date(2026, 8, 3))["id"].values


def test_amendment_rollback(fixture_db):
    store = _store(fixture_db)
    early = store.notices_asof(date(2026, 8, 10))
    assert early.loc[early["id"] == 4, "employees_affected"].iloc[0] == 80
    late = store.notices_asof(date(2026, 8, 25))
    assert late.loc[late["id"] == 4, "employees_affected"].iloc[0] == 150


def test_asof_monotonicity(fixture_db):
    store = _store(fixture_db)
    a = set(store.notices_asof(date(2025, 8, 1))["id"])
    b = set(store.notices_asof(date(2026, 9, 1))["id"])
    assert a <= b


def test_impute_notices():
    df = pd.DataFrame(
        {
            "state": ["CA", "NJ", "CA"],
            "notice_date": ["2025-06-02", None, "2025-06-09"],
            "effective_date": ["2025-08-01", "2025-09-15", None],
            "employees_affected": [100, None, 40],
        }
    )
    out = impute_notices(df)
    assert not out.loc[0, "announced_imputed"]
    assert out.loc[1, "announced_imputed"]
    assert out.loc[1, "announced_date"] == pd.Timestamp("2025-07-17")
    assert out.loc[2, "effective_imputed"]
    assert out.loc[2, "effective_date_final"] == pd.Timestamp("2025-08-08")
    # NJ has no non-null workers -> falls back to global median
    assert out.loc[1, "workers"] == 70.0
    assert out.loc[1, "workers_imputed"]


def test_weekly_panel_and_aggregate(fixture_db):
    store = _store(fixture_db)
    notices = store.notices_asof(date(2026, 9, 1))
    panel = build_weekly_panel(notices, date(2025, 5, 1), date(2026, 9, 1))
    # notice 1 announced week: 2025-06-02 (Mon) -> Sat 2025-06-07
    row = panel.loc[(pd.Timestamp("2025-06-07"), "CA")]
    assert row["announced_workers"] == 100.0
    # NJ notice has imputed announced date -> excluded from announced series
    nj = panel.xs("NJ", level="state")["announced_workers"]
    assert nj.fillna(0).sum() == 0.0
    # but present in effective series in week of 2025-09-15 (Mon) -> Sat 09-20
    assert panel.loc[(pd.Timestamp("2025-09-20"), "NJ"), "effective_workers"] == 50.0
    # aggregate over balanced set sums states
    states = balanced_states(panel, date(2025, 7, 1), date(2026, 9, 1))
    agg = national_aggregate(panel, states or ["CA", "TX"])
    assert (agg["effective"] >= 0).all()
