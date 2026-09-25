"""California EDD site matching must not invent a source-backed address."""

from dataclasses import replace

import openpyxl
import pytest

from warnlive.enrich.ca_address import CaMatcher, CaRecord, match_ca_notice, parse_xlsx


def record(**changes):
    base = CaRecord(
        company="Prospect Medical Holdings, Inc.", notice_date="2024-07-01",
        received_date="2024-07-02", effective_date="2024-08-30",
        workers=42, county="Los Angeles County",
        address="123 Main Street Los Angeles CA 90001", source_file="report.pdf",
        source_sha256="a" * 64, locators=("page:1/table:1/row:2",),
    )
    return replace(base, **changes)


def notice(**changes):
    base = dict(employer_name="Prospect Medical Holdings, Inc.",
                notice_date="2024-07-01", effective_date="2024-08-30",
                employees_affected=42, county="Los Angeles")
    return base | changes


def test_exact_match_retains_source_evidence():
    result = match_ca_notice(notice(), [record()])
    assert result.status == "matched"
    assert result.record.address == "123 Main Street Los Angeles CA 90001"
    assert result.record.source_sha256 == "a" * 64


def test_indexed_batch_match_has_same_result():
    matcher = CaMatcher([record(), record(company="Elsewhere")])
    assert matcher.match(notice()) == match_ca_notice(notice(), matcher)
    assert matcher.match(notice()).status == "matched"


def test_prospect_identical_duplicate_coalesces_all_locators():
    a = record()
    b = replace(a, locators=("page:3/table:1/row:8",))
    result = match_ca_notice(notice(), [a, b])
    assert result.status == "matched"
    assert result.record.locators == ("page:1/table:1/row:2", "page:3/table:1/row:8")


def test_multi_site_conflict_remains_unresolved():
    result = match_ca_notice(notice(), [record(), record(address="456 Oak Street Los Angeles CA 90002")])
    assert (result.status, result.reason) == ("unresolved", "conflicting_addresses")
    assert len(result.candidates) == 2


@pytest.mark.parametrize("change,reason", [
    ({"employees_affected": 41}, "effective_date_or_workers_mismatch"),
    ({"effective_date": "2024-08-31"}, "effective_date_or_workers_mismatch"),
    ({"county": "Orange County"}, "county_mismatch"),
    ({"notice_date": "2024-07-02"}, "no_employer_notice_date_match"),
    ({"effective_date": None}, "missing_effective_date_or_workers"),
])
def test_discriminator_mismatch(change, reason):
    result = match_ca_notice(notice(**change), [record()])
    assert (result.status, result.reason) == ("unresolved", reason)


def test_missing_county_cannot_resolve_sites_in_different_counties():
    rows = [record(), record(county="Orange County")]
    result = match_ca_notice(notice(county=None), rows)
    assert (result.status, result.reason) == ("unresolved", "missing_county_discriminator")


def test_same_address_but_nonidentical_rows_do_not_coalesce():
    rows = [record(), record(received_date="2024-07-03")]
    result = match_ca_notice(notice(), rows)
    assert (result.status, result.reason) == ("unresolved", "nonidentical_source_rows")


def test_report_county_address_role_conflict_is_held():
    source = record(
        company="Prospect Medical Systems, LLC", notice_date="2025-06-20",
        received_date="2025-06-23", effective_date="2025-07-01", workers=125,
        county="San Bernardino County",
        address="600 City Parkway West, 10th Floor Orange CA 92868",
    )
    filing = notice(
        employer_name="Prospect Medical Systems, LLC", notice_date="2025-06-20",
        effective_date="2025-07-01", employees_affected=125,
        county="San Bernardino County",
    )
    result = match_ca_notice(filing, [source])
    assert (result.status, result.reason) == ("unresolved", "role_conflict")


@pytest.mark.parametrize("address", [
    "17 2nd Street Cresco IL 52136",
    "100 Main Street FWHB NV 89101",
    "250 Commerce Drive Surfair TX 75001",
    "250 Commerce Drive Surfair TX 75001 Store 42",
])
def test_out_of_state_report_address_is_held(address):
    result = match_ca_notice(notice(), [record(address=address)])
    assert (result.status, result.reason) == ("unresolved", "role_conflict")


def test_ca_address_with_trailing_store_label_can_match():
    result = match_ca_notice(notice(), [record(
        address="123 Main Street Los Angeles CA 90001 Store 42")])
    assert result.status == "matched"


def _workbook(path, header):
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Detailed WARN Report "
    sheet.append(header)
    sheet.append(["Los Angeles County", "07/01/2024", "07/02/2024",
                  "08/30/2024", "Prospect Medical Holdings, Inc.",
                  "Layoff Permanent", 42, "123 Main Street Los Angeles CA 90001"])
    wb.save(path)


def test_workbook_roles_and_locator(tmp_path):
    path = tmp_path / "warn_report1.xlsx"
    _workbook(path, ["County/Parish", "Notice Date", "Processed Date",
                     "Effective Date", "Company", "Layoff/Closure",
                     "No. Of Employees", "Address"])
    rows = parse_xlsx(path)
    assert len(rows) == 1
    row = rows[0]
    assert (row.notice_date, row.received_date, row.processed_date, row.effective_date) == (
        "2024-07-01", None, "2024-07-02", "2024-08-30")
    assert row.workers == 42 and row.county == "Los Angeles County"
    assert row.locators == ("sheet:Detailed WARN Report/row:2",)
    assert len(row.source_sha256) == 64


def test_parser_drift_fails_closed(tmp_path):
    path = tmp_path / "warn_report1.xlsx"
    _workbook(path, ["County/Parish", "Notice Date", "Processed Date",
                     "Effective Date", "Company", "Layoff/Closure",
                     "Workers Changed", "Address"])
    with pytest.raises(ValueError, match="header not found"):
        parse_xlsx(path)
