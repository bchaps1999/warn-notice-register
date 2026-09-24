import csv
import json
import sqlite3

from warnlive.verify.timing import audit


def test_timing_cohort_uses_null_precision_provisionally_and_excludes_month_unknown(tmp_path):
    db = tmp_path / "timing.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE notices (id INTEGER, dedupe_key TEXT, state TEXT, "
        "employer_name TEXT, location TEXT, notice_date TEXT, effective_date TEXT, "
        "effective_date_end TEXT, notice_date_precision TEXT, source_url TEXT, "
        "source_details TEXT)"
    )
    interval_missing_end = json.dumps({"effective_date_interpretation": "interval"})
    interval_reversed_end = json.dumps({"effective_date_interpretation": "interval"})
    quarantined_end = json.dumps({"effective_date_end_status": "before_start_review"})
    conn.executemany(
        "INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "null", "AA", "Null precision", "A", "2024-02-02", "2024-02-01",
             None, None, "https://source.test", interval_missing_end),
            (2, "day", "AA", "Explicit day", "B", "2024-01-01", "2024-01-10",
             "2024-01-09", "day", "https://source.test", interval_reversed_end),
            (3, "month", "AA", "Month precision", "C", "2024-03-01", "2024-03-15",
             None, "month", "https://source.test", None),
            (4, "unknown", "AA", "Unknown precision", "D", "2024-04-01", "2024-04-15",
             None, "unknown", "https://source.test", None),
            (5, "review", "AA", "Quarantined end", "E", "2024-05-01", "2024-05-15",
             None, None, "https://source.test", quarantined_end),
            (6, "year", "AA", "Year precision", "F", "2024-01-01", "2024-06-15",
             None, "year", "https://source.test", None),
        ],
    )
    conn.commit()
    conn.close()

    total, eligible = audit(db, tmp_path / "out")

    assert (total, eligible) == (6, 3)
    cohort = list(csv.DictReader((tmp_path / "out" / "provisional_day_timing_cohort.csv").open()))
    assert [row["dedupe_key"] for row in cohort] == ["null", "day", "review"]
    assert cohort[0]["eligibility_basis"] == "provisional_null_precision"
    assert cohort[0]["days_notice_to_start"] == "-1"
    assert cohort[1]["eligibility_basis"] == "explicit_day_precision"
    assert cohort[2]["date_review_flag"] == "before_start_review"

    summary = next(csv.DictReader((tmp_path / "out" / "date_quality_by_state_source.csv").open()))
    assert summary["precision_day"] == "1"
    assert summary["precision_month"] == "1"
    assert summary["precision_year"] == "1"
    assert summary["precision_unknown"] == "1"
    assert summary["precision_null"] == "2"
    assert summary["missing_interval_end"] == "1"
    assert summary["end_before_start"] == "1"
    assert summary["quarantined_end_before_start"] == "1"
    assert summary["eligible_negative_intervals"] == "1"


def test_source_supported_timing_requires_both_date_roles_and_holds_bad_ranges(tmp_path):
    db = tmp_path / "supported.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE notices (id INTEGER, dedupe_key TEXT, state TEXT, "
        "employer_name TEXT, location TEXT, notice_date TEXT, effective_date TEXT, "
        "effective_date_end TEXT, notice_date_precision TEXT, notice_date_basis TEXT, "
        "effective_date_precision TEXT, effective_date_basis TEXT, "
        "effective_date_end_precision TEXT, effective_date_end_basis TEXT, "
        "source_url TEXT, source_details TEXT)"
    )
    rows = [
        (1, "range", "AA", "A", "X", "2024-01-01", "2024-01-10", "2024-01-30",
         "day", "reported", "day", "reported", "day", "reported", "official",
         json.dumps({"effective_date_role": "reported_closing_and_action_start",
                     "employee_separation_phases": [{"date": "2024-01-30", "group": "management"}]})),
        (2, "negative", "AA", "B", "X", "2024-02-10", "2024-02-05", None,
         "day", "reported", "day", "reported", None, None, "official",
         json.dumps({"tx_annual_workbook": {
             "notice_date_role": "listed_warn_notice_date",
             "effective_date_role": "anticipated_layoff_date",
         }})),
        (3, "month", "AA", "C", "X", "2024-03-01", "2024-03-15", None,
         "month", "inferred_year_from_effective_date", "day", "reported", None, None,
         "official", None),
        (4, "unassessed", "AA", "D", "X", "2024-04-01", "2024-04-15", None,
         "day", "reported", None, None, None, None, "official", None),
        (5, "reversed", "AA", "E", "X", "2024-05-01", "2024-05-20", "2024-05-19",
         "day", "reported", "day", "reported", "day", "reported", "official", None),
        (6, "unsupported_basis", "AA", "F", "X", "2024-06-01", "2024-06-15", None,
         "day", "reported", "day", "unknown", None, None, "official", None),
    ]
    conn.executemany("INSERT INTO notices VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()

    assert audit(db, tmp_path / "out") == (6, 5)
    with (tmp_path / "out" / "source_supported_start_timing_cohort.csv").open() as fh:
        start = list(csv.DictReader(fh))
    with (tmp_path / "out" / "source_supported_end_timing_cohort.csv").open() as fh:
        end = list(csv.DictReader(fh))
    with (tmp_path / "out" / "date_eligibility_by_notice.csv").open() as fh:
        eligibility = {r["dedupe_key"]: r for r in csv.DictReader(fh)}
    assert [(r["dedupe_key"], r["days_notice_to_start"]) for r in start] == [
        ("range", "9"), ("negative", "-5")
    ]
    assert [(r["dedupe_key"], r["days_notice_to_end"]) for r in end] == [
        ("range", "29")
    ]
    assert start[0]["effective_date_role"] == "reported_closing_and_action_start"
    assert json.loads(start[0]["employee_separation_phases"]) == [
        {"date": "2024-01-30", "group": "management"}
    ]
    assert eligibility["range"]["effective_date_role"] == "reported_closing_and_action_start"
    assert start[1]["notice_date_role"] == "listed_warn_notice_date"
    assert start[1]["effective_date_role"] == "anticipated_layoff_date"
    assert eligibility["negative"]["end_status"] == "date_missing"
    assert eligibility["month"]["start_status"] == "notice_date_not_day"
    assert eligibility["unassessed"]["start_status"] == "precision_unassessed"
    assert eligibility["reversed"]["start_status"] == "date_review_hold"
    assert eligibility["unsupported_basis"]["start_status"] == "basis_not_source_supported"
