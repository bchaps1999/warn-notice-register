import json

import pytest

from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest
from warnlive.store.site_export import build_site


@pytest.fixture()
def conn(tmp_path):
    conn = db_mod.connect(tmp_path / "test.sqlite")
    db_mod.init_db(conn)
    return conn


def record(i, **overrides):
    base = {
        "state": "CT",
        "employer_name": f"Employer {i}",
        "location": "Hartford, CT",
        "notice_date": f"2026-0{1 + i % 6}-15",
        "effective_date": "2026-08-01",
        "employees_affected": 10 * (i + 1),
        "layoff_type": "closure" if i % 2 else "mass_layoff",
        "is_temporary": None,
        "is_amendment": 0,
        "source_url": "https://example.gov",
        "source_notice_id": f"src-{i}",
        "raw_extra": "{}",
        "dedupe_key": f"{i:040x}",
        "raw_record_hash": f"hash-{i}",
    }
    base.update(overrides)
    return base


def test_build_site_manifest(conn, tmp_path):
    records = [record(i) for i in range(10)]
    ingest(conn, records, "2026-07-01")
    # amend one record
    ingest(conn, [record(3, employees_affected=999, raw_record_hash="hash-3b")], "2026-07-08")

    out = tmp_path / "out"
    counts = build_site(conn, load_registry(), out)

    meta = json.loads((out / "meta.json").read_text())
    assert meta["totals"]["notices"] == 10
    assert meta["key_prefix_len"] >= 8
    assert "CT" in meta["states"]

    national = json.loads((out / "national.json").read_text())
    assert sum(m["notices"] for m in national["monthly"]) == 10
    assert national["top_employers_12mo"]

    ct = json.loads((out / "states" / "ct.json").read_text())
    assert ct["coverage"]["notices"] == 10
    assert ct["monthly"]

    index = json.loads((out / "index.json").read_text())
    lens = {len(v) for v in index["columns"].values()}
    assert lens == {10}
    assert len(set(index["columns"]["key"])) == 10

    # round-trip the amended notice through its shard
    key = record(3)["dedupe_key"]
    shard = json.loads((out / "notices" / f"{key[:2]}.json").read_text())
    rec = shard[key]
    assert rec["employees_affected"] == 999
    assert len(rec["versions"]) == 2
    assert rec["key"] == key[: meta["key_prefix_len"]]

    # all 256 shards exist
    assert len(list((out / "notices").glob("*.json"))) == 256
    assert counts["index.json"] > 0
