import csv
import json
import sqlite3

import pytest

from warnlive.verify.release import verify


def _fixture(tmp_path):
    db = tmp_path / "warn.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notices (dedupe_key TEXT PRIMARY KEY, state TEXT, "
                 "notice_date TEXT, effective_date TEXT, employees_affected INTEGER)")
    conn.execute("CREATE TABLE source_observations (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO notices VALUES ('agency:1','OH',NULL,'2025-01-01',45)")
    conn.execute("INSERT INTO source_observations VALUES (1)")
    conn.commit()
    conn.close()
    exports = tmp_path / "exports"
    (exports / "states").mkdir(parents=True)
    with (exports / "warn_notices.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(("dedupe_key", "state", "notice_date", "effective_date", "employees_affected"))
        w.writerow(("agency:1", "OH", "", "2025-01-01", "45"))
    (exports / "states" / "oh.csv").write_bytes((exports / "warn_notices.csv").read_bytes())
    (exports / "source_observations.csv").write_text("id\n1\n")
    site = tmp_path / "site"
    site.mkdir()
    (site / "meta.json").write_text(json.dumps({
        "totals": {"notices": 1, "workers": 45}, "states": {"OH": {"notices": 1}},
    }))
    return db, exports, site


def test_release_reconciliation_accepts_matching_artifacts(tmp_path):
    db, exports, site = _fixture(tmp_path)
    assert verify(db, exports, site) == {
        "notices": 1, "workers": 45, "states": 1, "source_observations": 1,
    }


def test_release_reconciliation_rejects_stale_csv_and_site(tmp_path):
    db, exports, site = _fixture(tmp_path)
    (exports / "warn_notices.csv").write_text(
        "dedupe_key,state,notice_date,effective_date,employees_affected\n")
    with pytest.raises(ValueError, match="national export"):
        verify(db, exports, site)
    again = tmp_path / "again"
    again.mkdir()
    db, exports, site = _fixture(again)
    (site / "meta.json").write_text(json.dumps({
        "totals": {"notices": 2, "workers": 45}, "states": {"OH": {"notices": 1}},
    }))
    with pytest.raises(ValueError, match="site totals"):
        verify(db, exports, site)
