import json

import pytest

from warnlive.registry import load_registry
from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest
from warnlive.store.site_export import (
    FLAG_MONTH_DATE, _build_employer_shards, _county_series, _fnv_shard, _shift_months,
    build_site,
)


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


def test_old_employer_url_key_reads_the_new_group(tmp_path):
    redirects = tmp_path / "redirects.csv"
    redirects.write_text("old_key,new_key\nqid:old,ein:new\n")
    notice = {
        "employer_key": "ein:new", "employer_name": "Acme", "canonical_name": "Acme",
        "display_date": "2026-01-01", "dedupe_key": "a" * 40, "state": "CT",
        "location": "Hartford", "notice_date": "2026-01-01",
        "effective_date": "2026-02-01", "employees_affected": 10,
        "layoff_type": "closure", "cik": None, "ticker": None, "ein": "123",
        "lei": None, "wikidata_qid": None, "parent_company": None,
        "sic_description": None,
    }
    out = tmp_path / "employers"
    _build_employer_shards([notice], out, 8, redirects)
    old = json.loads((out / f"{_fnv_shard('qid:old')}.json").read_text())
    assert old["qid:old"]["key"] == "ein:new"
    assert old["qid:old"]["totals"]["notices"] == 1


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


def test_site_excludes_states_outside_csv_publication_scope(conn, tmp_path):
    ingest(conn, [record(1), record(2, state="AR")], "2026-07-01")
    out = tmp_path / "out"
    build_site(conn, load_registry(), out)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["totals"]["notices"] == 1


def test_pinned_as_of_makes_site_metadata_and_windows_repeatable(conn, tmp_path):
    ingest(conn, [record(1, notice_date="2026-07-15")], "2026-07-01")
    first = tmp_path / "first"
    second = tmp_path / "second"
    build_site(conn, load_registry(), first, as_of="2026-08-01")
    build_site(conn, load_registry(), second, as_of="2026-08-01")
    for filename in ("meta.json", "national.json", "states/ct.json"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()
    assert json.loads((first / "meta.json").read_text())["built_at"] == "2026-08-01T00:00:00Z"
    assert json.loads((first / "national.json").read_text())["anchor_date"] == "2026-08-01"


def test_site_reproduces_unpinned_build_timestamp(conn, tmp_path):
    ingest(conn, [record(1)], "2026-07-01")
    first, second = tmp_path / "first", tmp_path / "second"
    build_site(conn, load_registry(), first)
    built_at = json.loads((first / "meta.json").read_text())["built_at"]
    build_site(conn, load_registry(), second, built_at=built_at)
    assert (first / "meta.json").read_bytes() == (second / "meta.json").read_bytes()
    assert (first / "national.json").read_bytes() == (second / "national.json").read_bytes()


@pytest.mark.parametrize("anchor,months,expected", [
    ("2026-09-24", 12, "2025-09-24"),
    ("2024-02-29", 12, "2023-02-28"),
    ("2024-03-31", 1, "2024-02-29"),
    ("2026-03-31", 1, "2026-02-28"),
])
def test_calendar_anniversary_clamps_month_end(anchor, months, expected):
    assert _shift_months(anchor, months) == expected


def test_trailing_windows_use_build_day_and_exclude_future_action_dates(conn, tmp_path):
    ingest(conn, [
        record(1, notice_date="2026-01-01", effective_date="2026-12-01"),
        record(2, notice_date=None, effective_date="2026-09-23"),
        record(3, notice_date=None, effective_date="2026-10-01"),
        record(4, notice_date="2020-01-01", effective_date="2020-02-01"),
    ], "2026-09-24")
    out = tmp_path / "out"
    build_site(conn, load_registry(), out, as_of="2026-09-24")
    national = json.loads((out / "national.json").read_text())
    state = json.loads((out / "states" / "ct.json").read_text())
    assert national["anchor_date"] == "2026-09-24"
    assert national["date_basis_12mo"] == {"notice": 1, "action_fallback": 1}
    assert sum(row["notices"] for row in national["states_12mo"]) == 2
    assert sum(row["notices"] for row in national["top_employers_12mo"]) == 2
    assert sum(row["notices"] for row in state["top_employers_24mo"]) == 2
    assert state["coverage"]["action_date_fallback"] == 2
    assert not any(
        record(3)["dedupe_key"].startswith(row["key"])
        for row in state["recent"]
    )


def test_trailing_window_is_empty_when_state_has_only_old_records(conn, tmp_path):
    ingest(conn, [record(1, notice_date="2020-01-01")], "2020-01-01")
    out = tmp_path / "out"
    build_site(conn, load_registry(), out, as_of="2026-09-24")
    national = json.loads((out / "national.json").read_text())
    state = json.loads((out / "states" / "ct.json").read_text())
    assert national["anchor_date"] == "2026-09-24"
    assert national["states_12mo"] == []
    assert state["top_employers_24mo"] == []


def test_pinned_as_of_rejects_invalid_date(conn, tmp_path):
    with pytest.raises(ValueError, match="as_of must be"):
        build_site(conn, load_registry(), tmp_path / "out", as_of="2026-02-30")


def test_multisite_total_is_not_assigned_to_the_first_county():
    rows = [
        {"county_fips": "13067", "county_name": "Cobb", "state": "GA",
         "employees_affected": 127,
         "source_details": json.dumps({
             "worker_allocation": "unresolved",
             "sites": [{"address": "First"}, {"address": "Second"}],
         })},
        {"county_fips": "13067", "county_name": "Cobb", "state": "GA",
         "employees_affected": 10, "source_details": None},
    ]
    assert _county_series(rows) == [{
        "fips": "13067", "county": "Cobb", "state": "GA",
        "notices": 1, "workers": 10,
    }]


def test_historical_nj_month_is_not_displayed_as_known_day(conn, tmp_path):
    ingest(conn, [record(1, state="NJ", notice_date="2026-01-01")], "2026-07-01")
    out = tmp_path / "out"
    build_site(conn, load_registry(), out)
    index = json.loads((out / "index.json").read_text())
    assert index["columns"]["flags"][0] & FLAG_MONTH_DATE
    key = record(1)["dedupe_key"]
    detail = json.loads((out / "notices" / f"{key[:2]}.json").read_text())[key]
    assert detail["notice_date_precision"] == "month"


def test_site_exports_effective_start_and_end_metadata(conn, tmp_path):
    from warnlive.normalize.engine import _record_hash

    rec = record(
        1, effective_date_end="2026-08-31",
        notice_date_precision="day", notice_date_basis="reported",
        effective_date_precision="day", effective_date_basis="reported",
        effective_date_end_precision="month",
        effective_date_end_basis="inferred_from_month",
    )
    rec["raw_record_hash"] = _record_hash(rec)
    ingest(conn, [rec], "2026-07-01")
    out = tmp_path / "out"
    build_site(conn, load_registry(), out, as_of="2026-09-01")
    index = json.loads((out / "index.json").read_text())
    cols = index["columns"]
    assert (cols["notice_precision"], cols["notice_basis"]) == (["day"], ["reported"])
    assert (cols["effective_precision"], cols["effective_basis"],
            cols["effective_end_precision"], cols["effective_end_basis"]) == (
                ["day"], ["reported"], ["month"], ["inferred_from_month"])
    key = rec["dedupe_key"]
    detail = json.loads((out / "notices" / f"{key[:2]}.json").read_text())[key]
    assert (detail["effective_date_precision"], detail["effective_date_basis"],
            detail["effective_date_end_precision"], detail["effective_date_end_basis"]) == (
                "day", "reported", "month", "inferred_from_month")
    summary, = json.loads((out / "states/ct.json").read_text())["recent"]
    assert summary["effective_date_end_precision"] == "month"
