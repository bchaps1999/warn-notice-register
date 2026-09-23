from pathlib import Path

import json
from datetime import date

import pytest

from warnlive.normalize.engine import _dedupe_key, _fold, _to_canonical, normalize_file

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_normalize_ct_fixture():
    result = normalize_file("ct", FIXTURES, "https://example.gov/ct")
    assert result.raw_rows == 3
    # One row has a hopeless date not in CT's correction table -> counted failure
    assert result.failed_rows == 1
    assert len(result.records) == 2

    rec = result.records[0]
    assert rec["state"] == "CT"
    assert rec["employer_name"] == "Acme Manufacturing Inc."
    assert rec["notice_date"] == "2026-06-01"
    assert rec["employees_affected"] == 120
    assert rec["dedupe_key"] and rec["raw_record_hash"]
    assert rec["source_url"] == "https://example.gov/ct"

    # Same employer+date+location in rows 1 and 3 -> same dedupe key
    assert result.records[0]["dedupe_key"] != result.records[1]["dedupe_key"]


def test_fold_normalizes_for_dedupe_key_only():
    assert _fold("Acme Manufacturing, Inc.") == _fold("ACME MANUFACTURING LLC")
    assert _fold(None) == ""


def test_verify_state_on_fixture():
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    cfg = load_registry()["ct"]
    result = normalize_file("ct", FIXTURES, cfg.source_url)
    verification = verify_state(cfg, FIXTURES / "ct.csv", result)
    by_name = {c.name: c.outcome for c in verification.checks}
    assert by_name["fetch_ok"] == "pass"
    # Fixture has 3 rows, far below CT's min_rows threshold -> fail
    assert by_name["row_count"] == "fail"
    # 1/3 rows failed parse -> above 10% threshold -> fail
    assert by_name["parse_failures"] == "fail"
    assert verification.verdict == "failed"


def test_range_endpoint_changes_semantic_version():
    validated = {
        "postal_code": "SC", "company": "Paper Co", "location": "Georgetown",
        "notice_date": date(2026, 2, 18), "effective_date": date(2026, 5, 1),
        "jobs": 126,
    }
    raw = {
        "notice_date": "2/18/2026", "effective_date": "5/1/2026",
        "effective_end_date": "12/31/2026", "address": "1480 International Dr",
        "county": "Georgetown", "source": "sc/2026.pdf",
    }
    first = _to_canonical(validated, raw, "https://example.gov")
    changed = _to_canonical(validated, {**raw, "effective_end_date": "1/31/2027"},
                            "https://example.gov")
    assert first["effective_date_end"] == "2026-12-31"
    assert first["dedupe_key"] == changed["dedupe_key"]
    assert first["raw_record_hash"] != changed["raw_record_hash"]


def test_georgia_multisite_details_do_not_guess_site_workers():
    validated = {
        "postal_code": "GA", "company": "Example", "location": "Acworth",
        "notice_date": None, "effective_date": date(2026, 9, 1), "jobs": 127,
    }
    raw = {
        "GA WARN ID": "GA202600004", "First Date of Separation": "09/01/2026",
        "Second Date of Separation": "10/01/2026",
        "First Location Address": "1000 Cherokee Pkwy",
        "Second Location Address": "1300 Cherokee Pkwy",
    }
    rec = _to_canonical(validated, raw, "https://example.gov")
    details = json.loads(rec["source_details"])
    assert rec["source_identity"] == "GA:GA202600004"
    assert len(details["dates"]) == 2
    assert len(details["sites"]) == 2
    assert details["worker_allocation"] == "unresolved"
    assert all(site["workers"] is None for site in details["sites"])


def test_ga_source_ids_keep_distinct_filings_apart_without_changing_other_states():
    base = {
        "state": "GA", "employer_name": "Same Company", "notice_date": None,
        "location": "Atlanta", "source_identity": "GA:1",
    }
    assert _dedupe_key(base) != _dedupe_key({**base, "source_identity": "GA:2"})
    assert _dedupe_key(base) == _dedupe_key({**base, "employer_name": "New Name"})
    assert _dedupe_key({**base, "state": "IA"}) == _dedupe_key(
        {**base, "state": "IA", "source_identity": "GA:2"}
    )


def test_sc_source_rows_keep_undated_events_apart():
    base = {
        "state": "SC", "employer_name": "Mill", "notice_date": None,
        "location": "Richland", "source_identity": "SC:sc/2025.pdf:1",
    }
    assert _dedupe_key(base) != _dedupe_key({**base, "source_identity": "SC:sc/2025.pdf:2"})


def test_kansas_record_number_survives_area_renaming_and_separates_filings():
    validated = {
        "postal_code": "KS", "company": "Same Company", "location": "Wichita",
        "notice_date": date(2026, 2, 18), "effective_date": None, "jobs": 50,
    }
    raw = {
        "record_number": "2300",
        "detail_page_url": "https://www.kansasworks.com/search/warn_lookups/2300",
    }
    first = _to_canonical(validated, raw, "https://example.gov")
    renamed = _to_canonical(
        {**validated, "location": "3 - Workforce Partnership"}, raw,
        "https://example.gov",
    )
    other = _to_canonical(
        validated,
        {**raw, "record_number": "2301", "detail_page_url": raw["detail_page_url"].replace("2300", "2301")},
        "https://example.gov",
    )
    assert first["source_identity"] == "KS:2300"
    assert first["dedupe_key"] == renamed["dedupe_key"]
    assert first["dedupe_key"] != other["dedupe_key"]
    assert json.loads(first["source_details"])["source_record_number"] == "2300"
    mismatch = _to_canonical(
        validated, {**raw, "detail_page_url": raw["detail_page_url"].replace("2300", "2301")},
        "https://example.gov",
    )
    assert not mismatch.get("source_identity")


@pytest.mark.parametrize(
    "state,field,text,interpretation,end",
    [
        ("FL", "Layoff Date", "1/31/2019\nthru\n2/28/2019", "interval", "2019-02-28"),
        ("OH", "Layoff Date(s)", "01/13/2023 to 01/27/2023", "interval", "2023-01-27"),
        ("MA", "DATE(S) OF LAYOFFS", "6/30/2022; 7/31/2022", "list_or_phases", None),
        ("NJ", "Effective Date", "3/31/26 (Paramus), 4/30/26 (Livingston)", "list_or_phases", None),
        ("PA", "date_effective", "Beginning: 2/28/23 - Ending: 12/31/23", "interval", "2023-12-31"),
    ],
)
def test_raw_date_components_preserve_interval_vs_list(state, field, text, interpretation, end):
    validated = {
        "postal_code": state, "company": "Example", "location": "Town",
        "notice_date": date(2023, 1, 1), "effective_date": date(2023, 2, 1),
        "jobs": 12,
    }
    rec = _to_canonical(validated, {field: text}, "https://example.gov")
    details = json.loads(rec["source_details"])
    assert details["effective_date_interpretation"] == interpretation
    assert len(details["dates"]) >= 2
    assert rec.get("effective_date_end") == end
