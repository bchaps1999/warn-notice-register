import csv

from warnlive.store import db
from warnlive.store.dedupe import ingest
from warnlive.store.export import export_csvs


def test_historical_nj_csv_marks_month_precision(tmp_path):
    conn = db.connect(tmp_path / "test.sqlite")
    db.init_db(conn)
    rec = {
        "state": "NJ", "employer_name": "Example", "location": "Newark",
        "notice_date": "2026-01-01", "effective_date": "2026-03-01",
        "employees_affected": 20, "layoff_type": "mass_layoff",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": "old-hash",
        "raw_extra": "{}", "dedupe_key": "key-1", "raw_record_hash": "hash-1",
    }
    ingest(conn, [rec], "2026-07-01")
    export_csvs(conn, tmp_path / "exports", ["nj"])
    with (tmp_path / "exports" / "warn_notices.csv").open(newline="") as fh:
        row = next(csv.DictReader(fh))
    assert row["notice_date_precision"] == "month"
    assert row["notice_date_basis"] == "inferred_year_from_effective_date"
    assert row["effective_date_precision"] == ""
    assert row["effective_date_end_precision"] == ""


def test_csv_exports_effective_start_and_end_metadata(tmp_path):
    from warnlive.normalize.engine import _record_hash

    conn = db.connect(tmp_path / "test.sqlite")
    db.init_db(conn)
    rec = {
        "state": "NY", "employer_name": "Example", "location": "Getzville",
        "notice_date": "2024-03-21", "effective_date": "2024-06-30",
        "effective_date_end": "2024-06-30",
        "effective_date_precision": "day", "effective_date_basis": "reported",
        "effective_date_end_precision": "day", "effective_date_end_basis": "reported",
        "employees_affected": 65, "layoff_type": "closure",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": "source",
        "raw_extra": "{}", "dedupe_key": "key",
    }
    rec["raw_record_hash"] = _record_hash(rec)
    ingest(conn, [rec], "2026-09-23")
    export_csvs(conn, tmp_path / "exports", ["ny"])
    for path in (tmp_path / "exports/warn_notices.csv",
                 tmp_path / "exports/states/ny.csv"):
        with path.open(newline="") as fh:
            row = next(csv.DictReader(fh))
        assert (row["effective_date_precision"], row["effective_date_basis"],
                row["effective_date_end_precision"], row["effective_date_end_basis"]) == (
                    "day", "reported", "day", "reported")


def test_export_keeps_unadmitted_source_observation_separate(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    conn.execute("""INSERT INTO source_observations
        (source_bundle_sha256, state, observation_kind, source_artifact,
         source_row, source_row_sha256, raw_json,
         deterministic_json, admission_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("bundle", "IA", "notice", "agency/ia/historical-2023.pdf", "p1:r1",
         "source", '{"company_text":"Example"}',
         '{"employer":"Example"}', "identity_unresolved"))
    counts = export_csvs(conn, tmp_path / "exports", ["ia"])
    with (tmp_path / "exports" / "source_observations.csv").open(newline="") as fh:
        observation = next(csv.DictReader(fh))
    assert observation["admission_status"] == "identity_unresolved"
    assert observation["notice_id"] == ""
    assert observation["notice_dedupe_key"] == ""
    assert counts[str(tmp_path / "exports" / "source_observations.csv")] == 1
    with (tmp_path / "exports" / "warn_notices.csv").open(newline="") as fh:
        assert list(csv.DictReader(fh)) == []
    conn.execute("DELETE FROM source_observations")
    export_csvs(conn, tmp_path / "exports", ["ia"])
    with (tmp_path / "exports" / "source_observations.csv").open(newline="") as fh:
        assert list(csv.DictReader(fh)) == []
