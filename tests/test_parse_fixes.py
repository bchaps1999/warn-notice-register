"""Row accounting and source-faithful parsing for confirmed audit failures.

Each case uses the real failing source cell from the v1.1.1 audit.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pkgutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from warnlive.normalize.corrections import audit, classify
from warnlive.normalize.engine import _clean_text, _dedupe_key, _to_canonical, get_transformer_class, normalize_file
from warnlive.normalize.nonnotice import employer_hold_reason, non_notice_reason

FIXTURES = Path(__file__).parent / "fixtures"


def _write(tmp_path: Path, postal: str, header: list[str], rows: list[list[str]]) -> Path:
    with open(tmp_path / f"{postal}.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)
    return tmp_path


def _notes(rec: dict) -> list[dict]:
    return json.loads(rec["source_details"])["parse_notes"]


CT_HEADER = ["affected_company", "layoff_locations", "warn_document_date",
             "layoff_dates", "number_of_impacted_workers"]


# 1. An unparseable optional field blanks that field and keeps the row.

@pytest.mark.parametrize("jobs", ["approximately 50", "50-75"])
def test_unparseable_worker_count_is_blanked_not_dropped(tmp_path, jobs):
    _write(tmp_path, "ct", CT_HEADER, [["Acme Inc.", "Hartford, CT", "2026-06-01",
                                        "2026-08-01", jobs]])
    result = normalize_file("ct", tmp_path, None, observed_at="2026-09-24")
    assert (result.raw_rows, result.failed_rows, len(result.records)) == (1, 0, 1)
    rec = result.records[0]
    assert rec["employees_affected"] is None
    assert rec["notice_date"] == "2026-06-01"
    note = _notes(rec)[0]
    assert (note["field"], note["source_text"], note["action"]) == ("jobs", jobs, "blanked")
    assert json.loads(rec["raw_extra"])["number_of_impacted_workers"] == jobs


def test_nm_month_range_layoff_date_keeps_conduent(tmp_path):
    _write(tmp_path, "nm", ["NOTICE DATE", "JOB SITE NAME", "COUNTY NAME", "WDA NAME",
                            "TOTAL LAYOFF NUMBER", "LAYOFF DATE", "RECEIVED DATE", "CITY NAME"],
           [["6/29/26", "Conduent", "Bernalillo", "Central Region", "11",
             "July - August 2026", "6/29/26", "Albuquerque"]])
    result = normalize_file("nm", tmp_path, None, observed_at="2026-09-24")
    assert result.failed_rows == 0
    rec = result.records[0]
    assert (rec["employer_name"], rec["notice_date"], rec["effective_date"]) == (
        "Conduent", "2026-06-29", None)
    assert _notes(rec)[0]["source_text"] == "July - August 2026"


def test_ri_three_digit_year_keeps_asm_global(tmp_path):
    _write(tmp_path, "ri", ["WARN Date", "Date Received", "Company Name",
                            "Location of Layoffs", "Number Affected", "Effective Date",
                            "Closing Yes/No", "Union Yes/No", "Union Address"],
           [["5/4/204", "2024-05-09 00:00:00", "ASM GLOBAL", "Providence", "1029",
             "2024-06-30 00:00:00", "No", "Yes", ""]])
    result = normalize_file("ri", tmp_path, None, observed_at="2026-09-24")
    assert result.failed_rows == 0
    rec = result.records[0]
    assert rec["notice_date"] is None and rec["effective_date"] == "2024-06-30"
    assert _notes(rec)[0] == {
        "field": "notice_date", "source_text": "5/4/204", "rule": "unparseable_optional_field_v1",
        "action": "blanked", "reason": "unparseable_date", "error": "KeyError",
    }
    # An unread notice date keys on its text, not on "no date".
    assert json.loads(rec["source_details"])["notice_key_source_text"] == "5/4/204"


def test_future_date_check_is_pinned_to_observed_day(tmp_path):
    far = (date.today() + timedelta(days=500)).strftime("%Y-%m-%d")
    _write(tmp_path, "ct", CT_HEADER, [["Acme Inc.", "Hartford, CT", "2026-06-01", far, "10"]])
    # Observed earlier: the date is > 365 days after observation -> blanked.
    old = normalize_file("ct", tmp_path, None, observed_at="2026-01-01").records[0]
    assert old["effective_date"] is None and old["notice_date"] == "2026-06-01"
    assert [n["field"] for n in _notes(old)] == ["effective_date"]
    # Observed near that day: within the window -> kept, whatever today is.
    near = (date.today() + timedelta(days=400)).isoformat()
    kept = normalize_file("ct", tmp_path, None, observed_at=near).records[0]
    assert kept["effective_date"] == far


# 2. Upstream date corrections are audited against their own cells.

TODAY = date(2026, 9, 24)


@pytest.mark.parametrize("postal,key,action,used", [
    ("nj", "08/15/2023, 8/22/2023", "literal", "2023-08-15"),   # year changed upstream
    ("al", "01/01/0001", "null", None),                          # placeholder
    ("ca", "07/04/2002", "null", None),                          # below CA's floor
    ("ky", "03/19/2012 - 04/01/2012 ", "literal", "2012-03-19"),
    ("md", "7/24/1969", "null", None),
    ("co", "3/29", "null", None),                                # year invented
    ("tn", "November 9 through November 23, 2019", "first_literal", "2019-11-09"),
    ("co", "Downsize 1/26/20", "keep", "2020-01-26"),            # reformat only
    ("ak", "June-August 2023", "keep", "2023-06-01"),
    ("oh", "4/22/20266/20/2026", "keep", "2026-04-22"),
    ("nj", "4/5/24, 3/31/24", "keep", "2024-03-31"),             # earliest of a list
])
def test_upstream_correction_audit(postal, key, action, used):
    cls = get_transformer_class(postal)
    decision = audit(cls, TODAY)[key]
    assert decision.action == action
    assert (decision.used.isoformat() if decision.used else None) == used


def test_linked_document_corrections_are_kept_but_not_read_as_cells():
    decision = classify("https://example.gov/2022-04-13_WARN.pdf", datetime(2022, 6, 24),
                        minimum_year=1988, max_future_days=365, today=TODAY)
    assert (decision.action, decision.used) == ("keep_linked_document", date(2022, 6, 24))


def test_upstream_correction_tables_are_pinned():
    """Fails when an upstream bump adds or changes a correction: re-audit it
    (see warnlive/normalize/corrections.py) and refresh the fixture."""
    import warn_transformer.transformers as T

    expected = json.loads((FIXTURES / "upstream_date_corrections.json").read_text())
    actual = {}
    for name in sorted(m.name for m in pkgutil.iter_modules(T.__path__)):
        cls = get_transformer_class(name)
        items = sorted((str(k), v.isoformat() if v is not None else None)
                       for k, v in cls.date_corrections.items())
        if items:
            actual[name] = {"count": len(items), "sha256": hashlib.sha256(
                json.dumps(items, ensure_ascii=False).encode()).hexdigest()}
    assert actual == expected


def test_nj_year_changing_correction_publishes_the_cells_year(tmp_path):
    _write(tmp_path, "nj", ["Company", "City", "Month Posted", "Effective Date",
                            "Workforce Affected"],
           [["Morgan Stanley", "Jersey City", "June", "08/15/2023, 8/22/2023", "40"]])
    rec = normalize_file("nj", tmp_path, None, observed_at="2026-09-24").records[0]
    assert rec["effective_date"] == "2023-08-15"
    note = _notes(rec)[0]
    assert (note["upstream_value"], note["value"], note["action"]) == (
        "2024-08-15", "2023-08-15", "literal")


def test_rejected_notice_date_correction_keeps_the_notice_key():
    validated = {"postal_code": "UT", "company": "Example", "location": "Provo",
                 "notice_date": None, "effective_date": None, "jobs": 5}
    note = {"field": "notice_date", "source_text": "09/31/10",
            "rule": "upstream_date_correction_audit_v1", "action": "null",
            "reason": "first_date_unreadable", "upstream_value": "2010-09-30",
            "value": None, "precision": None}
    rec = _to_canonical(validated, {}, None, parse_notes=[note])
    before = _dedupe_key({"state": "UT", "employer_name": "Example",
                          "notice_date": "2010-09-30", "location": "Provo"})
    assert rec["notice_date"] is None
    assert rec["dedupe_key"] == before


# 3. Markup around a name is not the name's absence.

def test_html_wrapped_name_keeps_its_text():
    assert _clean_text("<b>Gamma Inc</b>") == "Gamma Inc"
    assert _clean_text("Company<br/><em>* footnote</em>") == "Company"


# 4. Nevada rows captured from the three-date layout.

NV_HEADER = ["Received Date", "Effective Date", "Type", "Affected Total", "Employer",
             "City", "County", "Notification"]


def test_nv_three_date_rows_are_read_under_their_printed_labels(tmp_path):
    _write(tmp_path, "nv", NV_HEADER, [
        ["2/18/2020", "2/4/2020", "4/12/2020", "Closure", "96", "Transform KM LLC",
         "Las Vegas", "Clark"],
        ["8/7/2024", "8/7/2024", "Layoff", "72", "Procaps Laboratories", "Henderson",
         "Clark", "WARN"],
    ])
    shifted, normal = normalize_file("nv", tmp_path, None, observed_at="2026-09-24").records
    assert (shifted["employer_name"], shifted["location"], shifted["employees_affected"],
            shifted["effective_date"], shifted["layoff_type"]) == (
        "Transform KM LLC", "Las Vegas, Clark", 96, "2020-04-12", "closure")
    details = json.loads(shifted["source_details"])
    assert details["agency_received_date"] == "2020-02-18"
    assert details["unlabeled_second_date_text"] == "2/4/2020"
    # The released key was built from the shifted cells; it is kept.
    assert shifted["dedupe_key"] == _dedupe_key({
        "state": "NV", "employer_name": "96", "location": "Transform KM LLC, Las Vegas",
        "notice_date": None,
        "source_details": json.dumps({"agency_received_date": "2020-02-18"}),
    })
    assert (normal["employer_name"], normal["employees_affected"]) == ("Procaps Laboratories", 72)
    assert "legacy_key_fields" not in json.loads(normal["source_details"])


# 5. Non-notice rows are held with a reason, never admitted.

NC_HEADER = ["County", "Warn Number", "Date of Notice", "Date Received by NC",
             "Effective Date", "WARN Notice: WARN Notice Name", "WARN notice type",
             "Type of layoff or closure", "Number affected at this location",
             "Address 1", "City"]


def test_nc_county_summary_rows_are_held(tmp_path):
    _write(tmp_path, "nc", NC_HEADER, [
        ["Brunswick County", "44", "0", "0", "0", "1", "0", "", "0", "", ""],
        ["11,644", "", "", "", "", "", "", "", "", "", ""],
        ["Nash County", "202100029", "12/29/2021", "12/30/2021", "02/01/2022",
         "QVC Rocky Mount, Inc- Distribution Center", "Closure", "Permanent", "1,953",
         "100 QVC Blvd. Rocky Mount NC 27815", ""],
    ])
    result = normalize_file("nc", tmp_path, None, observed_at="2026-09-24")
    assert [r["employer_name"] for r in result.records] == [
        "QVC Rocky Mount, Inc- Distribution Center"]
    # The totals row carries its total in County and nothing else.
    assert result.raw_rows == 3
    assert [f["hold_reason"] for f in result.failures] == [
        "nc_county_summary_count_row", "nc_county_summary_count_row"]
    assert result.held_rows == 2 and result.failure_rate == 0.0


def test_numeric_only_employer_is_held_for_review(tmp_path):
    _write(tmp_path, "nj", ["Company", "City", "Month Posted", "Effective Date",
                            "Workforce Affected"],
           [["1961", "Princeton junction", "June", "2020-03-20 00:00:00", "0"]])
    result = normalize_file("nj", tmp_path, None, observed_at="2026-09-24")
    assert result.records == []
    assert result.failures[0]["hold_reason"] == "employer_name_has_no_letters_review"
    assert employer_hold_reason("84 Lumber") is None


# 6. Hawaii amendments printed in the Date column.

def test_hi_starred_date_line_recovers_employer_and_amendment(tmp_path):
    _write(tmp_path, "hi", ["Company", "Date", "PDF url", "location", "jobs"], [
        ["", "* Hawaiian Airlines Amended September 16, 2020",
         "https://labor.hawaii.gov/wdc/files/2020/09/WARN-2020.09.16-HAL-Amended.pdf", "", ""],
        ["", "** Correction to FOH Hospitality Inc.",
         "https://labor.hawaii.gov/wdc/files/2020/09/09.14.2020-FOH-Hospitality-Inc.pdf", "", ""],
        ["", "*Errata to Amended WARN",
         "https://labor.hawaii.gov/wdc/files/2020/09/WARN-2020.09.21-HAL-Errata.pdf", "", ""],
    ])
    result = normalize_file("hi", tmp_path, None, observed_at="2026-09-24")
    hal, foh = result.records
    assert (hal["employer_name"], hal["notice_date"], hal["is_amendment"]) == (
        "Hawaiian Airlines", "2020-09-16", 1)
    assert (foh["employer_name"], foh["notice_date"], foh["is_amendment"]) == (
        "FOH Hospitality Inc.", None, 1)
    details = json.loads(hal["source_details"])
    assert details["amendment_marker"] == "amended"
    assert details["source_artifact"].endswith("HAL-Amended.pdf")
    # A line that names a document, not an employer, stays a counted failure.
    assert [f["prepared_row"] for f in result.failures] == [3]


# 7. Mississippi company cells carrying "City (County)".

def test_ms_site_is_split_from_company_cell(tmp_path):
    header = ["date_notice", "company", "workforce_area", "event_number", "naics",
              "action_type", "affected", "date_effective", "reason", "blank_entry",
              "city", "county"]
    _write(tmp_path, "ms", header, [
        ["03/01/2023", "Milwaukee Tool Clinton (Hinds)", "", "", "", "Closure", "30",
         "05/01/2023", "", "", "", ""],
        ["03/01/2023", "American Queen Steam Boat Company- Natchez New Albany (Indiana)",
         "", "", "", "Layoff", "10", "05/01/2023", "", "", "", ""],
        ["03/01/2024", "GXO Logistics", "", "", "", "Layoff", "12", "05/01/2024", "", "",
         "Southaven", "DeSoto"],
    ])
    tool, boat, gxo = normalize_file("ms", tmp_path, None, observed_at="2026-09-24").records
    assert (tool["employer_name"], tool["location"]) == ("Milwaukee Tool", "Clinton (Hinds)")
    assert tool["dedupe_key"] == _dedupe_key({
        "state": "MS", "employer_name": "Milwaukee Tool Clinton (Hinds)",
        "notice_date": "2023-03-01", "location": None})
    assert boat["employer_name"].endswith("New Albany (Indiana)")  # not a MS county
    assert boat["location"] is None
    assert (gxo["employer_name"], gxo["location"]) == ("GXO Logistics", "DeSoto")


# 8. PA ranges: unreadable or reversed starts do not overwrite the date.

@pytest.mark.parametrize("source,expected,status", [
    ("2/30/2025 - 3/15/2025", "2025-03-01", None),
    ("3/15/2025 - 3/01/2025", "2025-03-15", "reversed_range_review"),
])
def test_pa_range_start_needs_a_readable_ordered_range(source, expected, status):
    validated = {"postal_code": "PA", "company": "Example", "location": "Town",
                 "notice_date": None, "effective_date": date.fromisoformat(expected),
                 "jobs": 12}
    rec = _to_canonical(validated, {"date_effective": source}, None)
    details = json.loads(rec["source_details"])
    assert rec["effective_date"] == expected
    assert rec.get("effective_date_end") is None
    assert details.get("effective_date_status") == status


# 10. Agency test entries are held by an explicit, cited rule.

def test_agency_test_records_are_held():
    assert non_notice_reason("CA", {
        "notice_date": "06/16/2020", "effective_date": "06/30/2020",
        "received_date": "06/16/2020", "company": "Test Employer", "city": "Sacramento",
        "num_employees": "147", "layoff_or_closure": "Layoff Temporary",
        "county": "Sacramento County", "address": "",
        "source_file": "warn-report-for-7-1-2019-to-6-30-2020.pdf",
    }) == "apparent_agency_test_record"
    assert non_notice_reason("IL", {
        "IEBS Id": "20220502002", "Location Name": "ABC Manufacturing LLC",
        "Location Address": "100 W Randolph Street ",
    }) == "apparent_agency_test_record"
    # Not a name filter: a real "Testarossa Winery" row is untouched.
    assert non_notice_reason("CA", {"company": "Testarossa Winery", "city": "Los Gatos"}) is None
    assert non_notice_reason("IL", {"IEBS Id": "1", "Location Name": "ABC Manufacturing LLC",
                                    "Location Address": "1 Main St"}) is None
