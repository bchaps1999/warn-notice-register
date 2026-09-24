import csv
import json

import pytest

from warnlive.registry import load_registry
from warnlive.store import db
from warnlive.store.dedupe import ingest
from warnlive.store.export import export_csvs
from warnlive.store.links import export_links_csv
from warnlive.store.site_export import build_site
from warnlive.verify.release import verify


def _record():
    return {
        "state": "OH", "employer_name": "Acme", "location": "Columbus",
        "notice_date": "2026-01-01", "effective_date": "2026-03-01",
        "notice_date_precision": "day", "notice_date_basis": "reported",
        "employees_affected": 45, "layoff_type": "closure",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://agency.example/notice", "source_notice_id": "OH-1",
        "raw_extra": "{}", "dedupe_key": "a" * 40, "raw_record_hash": "hash-1",
    }


@pytest.fixture()
def published(tmp_path):
    db_path = tmp_path / "warn.sqlite"
    conn = db.connect(db_path)
    db.init_db(conn)
    ingest(conn, [_record()], "2026-01-02")
    conn.execute(
        "INSERT INTO source_observations "
        "(source_bundle_sha256,state,observation_kind,source_artifact,"
        "source_row,source_row_sha256,raw_json,deterministic_json,admission_status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("bundle", "OH", "notice", "agency/oh/file", "row-1", "hash", "{}", "{}",
         "event_unresolved"),
    )
    conn.commit()
    registry = load_registry()
    states = [cfg.postal for cfg in registry.all() if cfg.status in {"active", "archive"}]
    exports, site = tmp_path / "exports", tmp_path / "site"
    export_csvs(conn, exports, states)
    export_links_csv(conn, exports / "notice_links.csv")
    build_site(conn, registry, site, as_of="2026-09-24")
    conn.close()
    return db_path, exports, site


def _change_csv(path, column, value):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
        columns = list(rows[0])
    rows[0][column] = value
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_release_reconciliation_accepts_matching_artifacts(published):
    db_path, exports, site = published
    assert verify(db_path, exports, site) == {
        "notices": 1, "workers": 45, "states": 1, "source_observations": 1,
    }


@pytest.mark.parametrize("column,value", [
    ("employer_name", "Other company"),
    ("source_url", "https://wrong.example"),
    ("notice_date_precision", "month"),
    ("canonical_name", "Wrong annotation"),
])
def test_release_reconciliation_rejects_changed_national_fields(published, column, value):
    db_path, exports, site = published
    _change_csv(exports / "warn_notices.csv", column, value)
    with pytest.raises(ValueError, match="CSV export content"):
        verify(db_path, exports, site)


def test_release_reconciliation_rejects_changed_state_and_observation_rows(published):
    db_path, exports, site = published
    _change_csv(exports / "states" / "oh.csv", "location", "Cleveland")
    with pytest.raises(ValueError, match="states/oh.csv"):
        verify(db_path, exports, site)
    conn = db.connect(db_path)
    try:
        export_csvs(conn, exports,
                    [cfg.postal for cfg in load_registry().all()
                     if cfg.status in {"active", "archive"}])
    finally:
        conn.close()
    _change_csv(exports / "source_observations.csv", "admission_status", "rescinded")
    with pytest.raises(ValueError, match="source_observations.csv"):
        verify(db_path, exports, site)


def test_release_reconciliation_rejects_changed_site_detail_with_same_totals(published):
    db_path, exports, site = published
    shard = site / "notices" / "aa.json"
    payload = json.loads(shard.read_text())
    payload["a" * 40]["employer_name"] = "Other company"
    shard.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="site data content"):
        verify(db_path, exports, site)
