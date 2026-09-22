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
