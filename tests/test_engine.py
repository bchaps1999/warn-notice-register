from pathlib import Path

import hashlib
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
    assert len(result.failures) == 1
    assert result.failures[0]["prepared_row"] == 3
    assert "Gamma Logistics" in result.failures[0]["raw_extra"]
    assert result.failures[0]["source_row_sha256"]

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


def test_header_reordering_is_not_source_schema_drift(tmp_path):
    from dataclasses import replace
    from datetime import date

    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state
    from warnlive.normalize.engine import NormalizeResult

    raw = tmp_path / "co.csv"
    raw.write_text("b,a\n2,1\n")
    cfg = replace(load_registry()["co"], min_rows=1,
                  expected_columns=["a", "b"], staleness_days=None)
    norm = NormalizeResult(
        state="CO", raw_rows=1,
        records=[{
            "employer_name": "Example", "notice_date": "2026-09-01",
            "effective_date": "2026-10-01", "dedupe_key": "one",
        }],
    )

    result = verify_state(cfg, raw, norm, today=date(2026, 9, 23))
    schema = next(check for check in result.checks if check.name == "schema_drift")
    assert schema.outcome == "pass"
    assert schema.detail == "same columns in a different order"


@pytest.mark.parametrize("header,expected", [
    ("a,c\n1,2\n", "fail"),
    ("a,b,c\n1,2,3\n", "warn"),
])
def test_required_header_missing_fails_but_extra_header_warns(tmp_path, header, expected):
    from dataclasses import replace
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "source.csv"
    raw.write_text(header)
    cfg = replace(load_registry()["co"], min_rows=1,
                  expected_columns=["a", "b"], staleness_days=None)
    norm = NormalizeResult(state="CO", raw_rows=1, records=[{
        "employer_name": "Example", "notice_date": "2026-09-01",
        "effective_date": "2026-10-01", "dedupe_key": "one",
    }])
    result = verify_state(cfg, raw, norm, today=date(2026, 9, 23))
    assert next(c for c in result.checks if c.name == "schema_drift").outcome == expected


def test_effective_date_freshness_excludes_future_actions(tmp_path):
    from dataclasses import replace
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "pa.csv"
    raw.write_text("company\nExample\n")
    cfg = replace(load_registry()["pa"], min_rows=1,
                  expected_columns=["company"], staleness_days=90)
    norm = NormalizeResult(state="PA", raw_rows=2, records=[
        {"employer_name": "Past", "notice_date": None,
         "effective_date": "2026-01-01", "dedupe_key": "old"},
        {"employer_name": "Future", "notice_date": None,
         "effective_date": "2027-06-01", "dedupe_key": "future"},
    ])
    checks = {c.name: c for c in verify_state(
        cfg, raw, norm, today=date(2026, 9, 23)).checks}
    assert checks["date_sanity"].outcome == "pass"
    assert checks["freshness"].outcome == "warn"
    assert "future action dates excluded" in checks["freshness"].detail


def test_missing_configured_source_clock_records_freshness_warning(tmp_path):
    from dataclasses import replace
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "wa.csv"
    raw.write_text("company\nExample\n")
    cfg = replace(load_registry()["wa"], min_rows=1,
                  expected_columns=["company"])
    norm = NormalizeResult(state="WA", raw_rows=1, records=[{
        "employer_name": "Example", "notice_date": None,
        "effective_date": "2026-10-01", "source_details": "{}",
        "dedupe_key": "one",
    }])
    checks = {c.name: c for c in verify_state(
        cfg, raw, norm, today=date(2026, 9, 23)).checks}
    assert checks["date_sanity"].outcome == "fail"
    assert checks["freshness"].outcome == "warn"


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


def test_wa_agency_receipt_is_not_legal_notice_and_keeps_existing_key():
    validated = {
        "postal_code": "WA", "company": "Example Co", "location": "Redmond",
        "notice_date": date(2026, 7, 8), "effective_date": date(2026, 9, 4),
        "jobs": 605,
    }
    raw = {"Company": "Example Co", "Location": "Redmond",
           "Received Date": "7/8/2026", "Layoff Start Date": "9/4/2026"}
    rec = _to_canonical(validated, raw, "https://esd.wa.gov/warn")
    assert rec["notice_date"] is None
    assert rec["effective_date"] == "2026-09-04"
    details = json.loads(rec["source_details"])
    assert details["dates"][0]["role"] == "agency_received"
    assert details["agency_received_date"] == "2026-07-08"
    expected_key = hashlib.sha1(
        f"WA|{_fold('Example Co')}|2026-07-08|{_fold('Redmond')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key
    later = _to_canonical(validated, {**raw, "Received Date": "7/9/2026"},
                          "https://esd.wa.gov/warn")
    assert later["dedupe_key"] != rec["dedupe_key"]


@pytest.mark.parametrize("received,old_key_date,expected_role", [
    ("6/2/2026", date(2026, 6, 2), "agency_received"),
    ("", date(2026, 8, 1), "legacy_layoff_fallback"),
])
def test_mn_receipt_and_layoff_fallback_do_not_become_legal_notice(
    received, old_key_date, expected_role,
):
    validated = {
        "postal_code": "MN", "company": "Example Co", "location": "Duluth",
        "notice_date": old_key_date, "effective_date": date(2026, 8, 1),
        "jobs": 55,
    }
    raw = {"WARN Received": received, "Layoff Start": "8/1/2026",
           "WARN Act": "YES"}
    rec = _to_canonical(validated, raw, "https://mn.gov/deed")
    assert rec["notice_date"] is None
    details = json.loads(rec["source_details"])
    assert details["legacy_notice_key_date"] == old_key_date.isoformat()
    if expected_role == "legacy_layoff_fallback":
        assert details["legacy_notice_fallback"]["source_field"] == "Layoff Start"
    else:
        assert details["dates"][0]["role"] == expected_role
    expected_key = hashlib.sha1(
        f"MN|{_fold('Example Co')}|{old_key_date.isoformat()}|{_fold('Duluth')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key


def test_nv_received_date_keeps_identity_but_not_legal_notice():
    validated = {
        "postal_code": "NV", "company": "Food Source", "location": "Reno, Washoe",
        "notice_date": date(2021, 1, 12), "effective_date": date(2021, 3, 31),
        "jobs": 33,
    }
    raw = {"Received Date": "1/12/2021", "Effective Date": "3/31/2021",
           "Employer": "Food Source", "City": "Reno", "County": "Washoe"}
    rec = _to_canonical(validated, raw, "https://detr.nv.gov/Page/WARN")
    assert rec["notice_date"] is None
    assert rec["effective_date"] == "2021-03-31"
    details = json.loads(rec["source_details"])
    assert details["date_evidence_rule"] == "nv_agency_received_role_v1"
    assert details["dates"][0]["role"] == "agency_received"
    assert details["agency_received_date"] == "2021-01-12"
    expected_key = hashlib.sha1(
        f"NV|{_fold('Food Source')}|2021-01-12|{_fold('Reno, Washoe')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key


def test_ma_received_date_is_separate_from_filing_date_and_key_is_stable():
    validated = {
        "postal_code": "MA", "company": "GSK plc.",
        "location": "Cambridge, MA, Boston",
        "notice_date": date(2025, 7, 16),
        "effective_date": date(2025, 10, 4), "jobs": 150,
    }
    raw = {"RECEIVED": "7/16/2025", "DATE(S) OF LAYOFFS": "10/4/2025 - 3/31/2026"}
    rec = _to_canonical(validated, raw, "https://www.mass.gov/warn")
    assert rec["notice_date"] is None
    assert rec["effective_date"] == "2025-10-04"
    assert rec["effective_date_end"] == "2026-03-31"
    details = json.loads(rec["source_details"])
    assert details["agency_received_date"] == "2025-07-16"
    assert details["dates"][0]["role"] == "agency_received"
    expected_key = hashlib.sha1(
        f"MA|{_fold('GSK plc.')}|2025-07-16|{_fold('Cambridge, MA, Boston')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key


def test_ma_multiple_dates_in_received_cell_are_held_as_ambiguous():
    validated = {
        "postal_code": "MA", "company": "Sodexo", "location": "Boston",
        "notice_date": date(2021, 7, 7), "effective_date": date(2021, 7, 31),
        "jobs": 74,
    }
    raw = {"RECEIVED": "07/07/2021 - (08/30/2021)",
           "DATE(S) OF LAYOFFS": "7/31/2021"}
    rec = _to_canonical(validated, raw, "https://www.mass.gov/warn")
    details = json.loads(rec["source_details"])
    assert rec["notice_date"] is None
    assert details["agency_received_date"] is None
    assert details["received_date_status"] == "multiple_dates_in_received_field_review"
    assert [x["date"] for x in details["dates"][:2]] == ["2021-07-07", "2021-08-30"]
    expected_key = hashlib.sha1(
        f"MA|{_fold('Sodexo')}|2021-07-07|{_fold('Boston')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key


def test_ky_received_date_keeps_key_but_not_legal_notice():
    validated = {
        "postal_code": "KY", "company": "Carrier Corporation",
        "location": "Simpson", "notice_date": date(2026, 6, 12),
        "effective_date": date(2026, 8, 30), "jobs": 70,
    }
    raw = {"date_received": "2026-06-12 00:00:00",
           "date_effective": "2026-08-30 00:00:00"}
    rec = _to_canonical(validated, raw, "https://kcc.ky.gov/warn")
    assert rec["notice_date"] is None
    assert rec["effective_date"] == "2026-08-30"
    detail = json.loads(rec["source_details"])
    assert detail["date_evidence_rule"] == "ky_agency_received_role_v1"
    assert detail["agency_received_date"] == "2026-06-12"
    assert detail["dates"][0]["role"] == "agency_received"
    expected_key = hashlib.sha1(
        f"KY|{_fold('Carrier Corporation')}|2026-06-12|{_fold('Simpson')}".encode()
    ).hexdigest()
    assert rec["dedupe_key"] == expected_key


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


def test_kansas_out_of_state_contact_city_is_not_displayed_as_layoff_site():
    validated = {
        "postal_code": "KS", "company": "First Student", "location": "Cincinnati",
        "notice_date": date(2026, 5, 1), "effective_date": None, "jobs": 50,
    }
    raw = {
        "record_number": "2304", "city": "Cincinnati", "zip": "45202",
        "lwib_area": "1 - Kansas WorkforceONE",
        "detail_page_url": "https://www.kansasworks.com/search/warn_lookups/2304",
    }
    rec = _to_canonical(validated, raw, "https://www.kansasworks.com")
    assert rec["location"] == "1 - Kansas WorkforceONE"
    detail = json.loads(rec["source_details"])
    assert detail["listed_contact_city"] == "Cincinnati"
    assert detail["listed_contact_zip"] == "45202"
    assert detail["location_basis"] == "workforce_area_out_of_state_contact_zip"
    kansas = _to_canonical({**validated, "location": "Iola"},
                           {**raw, "city": "Iola", "zip": "66749"},
                           "https://www.kansasworks.com")
    assert kansas["location"] == "Iola"
    postal = _to_canonical(
        {**validated, "location": "P.O. # 98"},
        {**raw, "record_number": "920", "city": "", "zip": "",
         "address": "P.O. # 98", "detail_page_url": raw["detail_page_url"].replace("2304", "920")},
        "https://www.kansasworks.com",
    )
    assert postal["location"] == "1 - Kansas WorkforceONE"
    assert json.loads(postal["source_details"])["location_basis"] == (
        "workforce_area_postal_address_only"
    )


def test_illinois_export_id_survives_revisions_and_separates_records():
    validated = {
        "postal_code": "IL", "company": "Norvax, LLC", "location": "Chicago",
        "notice_date": date(2025, 11, 3), "effective_date": None, "jobs": 487,
    }
    raw = {
        "IEBS Id": "20251104001",
        "Initial Date Reported": "2025-11-03 00:00:00",
        "Last Report Date": "2025-11-03 00:00:00",
        "Notification Date(s)": "11/3/2025",
    }
    first = _to_canonical(validated, raw, "https://example.gov")
    revised = _to_canonical(
        {**validated, "company": "Norvax, LLC/GoHealth, LLC", "jobs": 598},
        raw, "https://example.gov",
    )
    other = _to_canonical(validated, {"IEBS Id": "20251104002"}, "https://example.gov")
    assert first["source_identity"] == "IL:IEBS:20251104001"
    assert first["notice_date"] is None
    assert first["dedupe_key"] == revised["dedupe_key"]
    assert first["raw_record_hash"] != revised["raw_record_hash"]
    assert first["dedupe_key"] != other["dedupe_key"]
    assert json.loads(first["source_details"])["identity_basis"] == "illinois_iebs_export_record"
    assert json.loads(first["source_details"])["agency_reported_date"] == "2025-11-03"


def test_illinois_reporting_and_notification_dates_are_not_legal_notice():
    validated = {
        "postal_code": "IL", "company": "Example", "location": "Chicago",
        "notice_date": date(2026, 1, 8), "effective_date": None, "jobs": 25,
    }
    raw = {
        "IEBS Id": "20260112001", "Initial Date Reported": "2026-01-08 00:00:00",
        "Last Report Date": "2026-08-21 00:00:00",
        "Notification Date(s)": "8/21/2026, invalid, 1/8/2026",
    }
    rec = _to_canonical(validated, raw, "https://example.gov")
    details = json.loads(rec["source_details"])
    assert rec["notice_date"] is None
    assert details["agency_reported_date"] == "2026-01-08"
    assert [(item["role"], item["date"]) for item in details["dates"]] == [
        ("agency_reported", "2026-01-08"),
        ("agency_last_reported", "2026-08-21"),
        ("agency_notification", "2026-08-21"),
        ("agency_notification", None),
        ("agency_notification", "2026-01-08"),
    ]
    assert details["dates"][3]["source_text"] == "invalid"
    assert details["notification_dates_text"] == raw["Notification Date(s)"]
    changed = _to_canonical(validated, {
        **raw, "Notification Date(s)": "9/1/2026, 1/8/2026",
    }, "https://example.gov")
    assert changed["dedupe_key"] == rec["dedupe_key"]
    assert changed["raw_record_hash"] != rec["raw_record_hash"]
    invalid = _to_canonical(validated, {
        **raw, "Initial Date Reported": "unparseable", "Last Report Date": "",
        "Notification Date(s)": "",
    }, "https://example.gov")
    assert invalid["notice_date"] is None
    assert json.loads(invalid["source_details"])["agency_reported_date"] is None


def test_illinois_health_checks_source_report_freshness_without_legal_date(tmp_path):
    from dataclasses import replace
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "il.csv"
    raw.write_text("IEBS Id,Initial Date Reported\n1,2026-09-16 00:00:00\n")
    cfg = replace(load_registry()["il"], min_rows=1, expected_columns=None)
    norm = NormalizeResult(state="IL", raw_rows=1, records=[{
        "employer_name": "Example", "notice_date": None, "effective_date": None,
        "source_details": json.dumps({"agency_reported_date": "2026-09-16"}),
        "dedupe_key": "one",
    }])
    result = verify_state(cfg, raw, norm, today=date(2026, 9, 23))
    checks = {check.name: check for check in result.checks}
    assert checks["date_sanity"].outcome == "pass"
    assert checks["freshness"].outcome == "pass"
    assert "agency_reported_date" in checks["freshness"].detail


@pytest.mark.parametrize(
    "state,field,text,interpretation,end",
    [
        ("FL", "Layoff Date", "1/31/2019\nthru\n2/28/2019", "interval", "2019-02-28"),
        ("OH", "Layoff Date(s)", "01/13/2023 to 01/27/2023", "interval", "2023-01-27"),
        ("MA", "DATE(S) OF LAYOFFS", "6/30/2022; 7/31/2022", "list_or_phases", None),
        ("KY", "date_effective", "01/03/2002 - 04/15/2002", "interval", "2002-04-15"),
        ("NJ", "Effective Date", "3/31/26 (Paramus), 4/30/26 (Livingston)", "list_or_phases", None),
        ("PA", "date_effective", "Beginning: 2/28/23 - Ending: 12/31/23", "interval", "2023-12-31"),
        ("PA", "date_effective", "Beginning 8/21/23 - Ending 9/19/23", "interval", "2023-09-19"),
        ("PA", "date_effective", "Commencing 8/21/23; Ending 9/19/23", "interval", "2023-09-19"),
        ("PA", "date_effective", "Beginning: 1/3/2024 (52 employees); ending: 3/31/2024 (128 employees)", "interval", "2024-03-31"),
        ("PA", "date_effective", "beginning 5/3/26, ending 5/31/26", "interval", "2026-05-31"),
        ("PA", "date_effective", "beginning: 1/6/2025; completed: 3/31/2025", "interval", "2025-03-31"),
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


def test_nj_raw_observation_identity_keeps_distinct_rows_without_notice_date():
    validated = {
        "postal_code": "NJ", "company": "Example", "location": "Newark",
        "notice_date": None, "effective_date": date(2024, 7, 1), "jobs": 20,
    }
    first = _to_canonical(validated, {"Company": "Example", "City": "Newark",
                                      "Month Posted": "June", "Effective Date": "7/1/24",
                                      "Workforce Affected": "20"}, "https://example.gov")
    changed = _to_canonical(validated, {"Company": "Example", "City": "Newark",
                                        "Month Posted": "June", "Effective Date": "7/1/24",
                                        "Workforce Affected": "21"}, "https://example.gov")
    repeat = _to_canonical(validated, {"Company": "Example", "City": "Newark",
                                       "Month Posted": "June", "Effective Date": "7/1/24",
                                       "Workforce Affected": "20"}, "https://example.gov")
    assert first["notice_date"] is None
    assert first["source_identity"].startswith("NJ:raw-row:")
    assert first["dedupe_key"] != changed["dedupe_key"]
    assert first["dedupe_key"] == repeat["dedupe_key"]
    assert json.loads(first["source_details"])["identity_basis"] == "raw_row_observation"


def test_nj_phase_list_with_earlier_component_is_held_for_start_review():
    validated = {"postal_code": "NJ", "company": "Example", "location": "Newark",
                 "notice_date": None, "effective_date": date(2024, 9, 27), "jobs": 20}
    rec = _to_canonical(validated, {"Month Posted": "August",
                                    "Effective Date": "9/27/24, 6/12/24"},
                        "https://example.gov")
    details = json.loads(rec["source_details"])
    assert rec["effective_date"] == "2024-09-27"
    assert rec.get("effective_date_end") is None
    assert details["effective_date_status"] == "phase_start_selection_review"


@pytest.mark.parametrize(
    "source,start,end",
    [
        ("Beginning 9/29/23; Ending 11/16/23", "2023-09-29", "2023-11-16"),
        ("5/26/25-5/30/25", "2025-05-26", "2025-05-30"),
    ],
)
def test_pa_interval_start_comes_from_unambiguous_source_range(source, start, end):
    validated = {
        "postal_code": "PA", "company": "Example", "location": "Town",
        "notice_date": None, "effective_date": date(2025, 1, 31), "jobs": 12,
    }
    rec = _to_canonical(validated, {"date_effective": source}, "https://example.gov")
    assert rec["effective_date"] == start
    assert rec["effective_date_end"] == end


def test_co_reversed_end_is_retained_for_review_without_canonical_range():
    validated = {
        "postal_code": "CO", "company": "Example", "location": "Town",
        "notice_date": date(2025, 4, 1), "effective_date": date(2025, 5, 31),
        "jobs": 12,
    }
    rec = _to_canonical(validated, {"begin_date": "5/31/25", "end_date": "5/21/25"},
                        "https://example.gov")
    assert rec["effective_date"] == "2025-05-31"
    assert rec.get("effective_date_end") is None
    details = json.loads(rec["source_details"])
    assert details["dates"][0]["date"] == "2025-05-21"
    assert details["effective_date_end_status"] == "before_start_review"
