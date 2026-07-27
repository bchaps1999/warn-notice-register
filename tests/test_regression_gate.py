"""The gate exists because two parse bugs reached published data: New York
worker counts read as 1.7 quadrillion, and Wisconsin locations replaced by a
date serial. Each case below is one of those, or the shape of one."""

from warnlive.store import db as db_mod
from warnlive.verify import regression


def _db(tmp_path, rows, name="t.sqlite"):
    """rows: (state, employer, location, jobs) — notice_date fixed."""
    conn = db_mod.connect(tmp_path / name)
    db_mod.init_db(conn)
    for i, (state, employer, location, jobs) in enumerate(rows):
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, employer_name, location, "
            "notice_date, employees_affected, layoff_type, current_version, "
            "first_seen, last_seen) VALUES (?,?,?,?,?,?,?,1,?,?)",
            (f"k{i}", state, employer, location, "2020-01-01", jobs, "closure",
             "2020-01-01", "2020-01-01"),
        )
    conn.commit()
    return conn


def _outcome(result, name):
    return next(c.outcome for c in result.checks if c.name == name)


def test_unchanged_data_passes(tmp_path):
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 60)
    snapshot = regression.build_snapshot(conn)
    assert regression.check_regressions(conn, snapshot).verdict == "ok"


def test_growth_passes(tmp_path):
    """Notices are only ever added; a normal scrape must not trip anything."""
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 60)
    snapshot = regression.build_snapshot(conn)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, employer_name, location, "
        "notice_date, employees_affected, layoff_type, current_version, "
        "first_seen, last_seen) VALUES ('new','WI','Beta','Racine','2020-02-01',"
        "75,'closure',1,'2020-02-01','2020-02-01')"
    )
    assert regression.check_regressions(conn, snapshot).verdict == "ok"


def test_absurd_headcount_fails_without_any_history(tmp_path):
    """The New York bug. The ceiling needs no snapshot to catch it."""
    conn = _db(tmp_path, [("NY", "Bank", "NYC", 1_716_912_988_271_000)])
    result = regression.check_regressions(conn, None)
    assert result.verdict == "failed"
    assert _outcome(result, "notice_size_ceiling") == "fail"


def test_emptied_field_fails(tmp_path):
    """The Wisconsin bug: a column-mapping error blanks a field for a state."""
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 60)
    snapshot = regression.build_snapshot(conn)
    conn.execute("UPDATE notices SET location = NULL WHERE state = 'WI'")
    result = regression.check_regressions(conn, snapshot)
    assert result.verdict == "failed"
    assert _outcome(result, "field_emptied") == "fail"


def test_moderate_null_drift_only_warns(tmp_path):
    """Sources do change; a partial shift should not stop the pipeline."""
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 100)
    snapshot = regression.build_snapshot(conn)
    conn.execute(
        "UPDATE notices SET employees_affected = NULL "
        "WHERE rowid IN (SELECT rowid FROM notices LIMIT 30)"
    )
    result = regression.check_regressions(conn, snapshot)
    assert result.verdict == "degraded"
    assert _outcome(result, "field_completeness") == "warn"


def test_vanished_state_fails(tmp_path):
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 60
               + [("KS", "Beta", "Topeka", 20)] * 60)
    snapshot = regression.build_snapshot(conn)
    conn.execute("DELETE FROM notices WHERE state = 'KS'")
    result = regression.check_regressions(conn, snapshot)
    assert result.verdict == "failed"
    assert _outcome(result, "total_notices") == "fail"


def test_small_state_churn_is_tolerated(tmp_path):
    """Below the floor, one withdrawn filing is a large percentage."""
    conn = _db(tmp_path, [("VT", "Acme", "Burlington", 5)] * 10)
    snapshot = regression.build_snapshot(conn)
    conn.execute("DELETE FROM notices WHERE rowid = (SELECT MIN(rowid) FROM notices)")
    result = regression.check_regressions(conn, snapshot)
    # The national total still fails — nothing should lose notices outright —
    # but the per-state ratio check must not pile on.
    assert _outcome(result, "state_notice_counts") == "pass"


def test_new_state_has_nothing_to_regress_against(tmp_path):
    conn = _db(tmp_path, [("WI", "Acme", "Madison", 50)] * 60)
    snapshot = regression.build_snapshot(conn)
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, employer_name, location, "
        "notice_date, employees_affected, layoff_type, current_version, "
        "first_seen, last_seen) VALUES ('pr1','PR','Nueva','San Juan','2020-03-01',"
        "40,'closure',1,'2020-03-01','2020-03-01')"
    )
    assert regression.check_regressions(conn, snapshot).verdict == "ok"
