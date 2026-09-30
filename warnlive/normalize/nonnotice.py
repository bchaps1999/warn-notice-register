"""Identify source rows that are not WARN notices.

A row is held here, with a named reason, only on explicit evidence:

* ``nc_county_summary_count_row``: the NC archive PDFs end with a "WARN
  Summary by County/Parish" count table. The archive parser keeps any row
  whose first numeric column is all digits, so each county count row (and
  the totals row, whose total lands in County) reached the CSV with small
  integers or blanks in every column but County, and no employer or date.
  Real NC rows carry an 8-9 digit WARN number, dates and a name.
* ``ne_layoff_closure_report_not_warn``: NE rows the collector tagged
  ``source_report = layoff_closure_report``: NDOL's general layoff/closure
  report (LayoffAndClosureReportData), not its WARN report.
* ``apparent_agency_test_record``: rows listed in ``_TEST_RECORDS``. Each
  entry matches the source row's own cells exactly and cites why it is an
  agency test entry. This is not a name filter.

``non_notice_reason`` is called by ``engine.normalize_file`` before the row
is transformed; the engine records the row as held (``hold_reason``) so it
stays in row accounting. ``employer_hold_reason`` applies to the transformed
employer name. Pure functions; no I/O.
"""

from __future__ import annotations

import re

_INTEGER = re.compile(r"\d*")

# (state, exact source cells, evidence). Cells are compared after strip().
_TEST_RECORDS: tuple[tuple[str, dict[str, str], str], ...] = (
    ("CA", {
        "company": "Test Employer", "city": "Sacramento", "num_employees": "147",
        "notice_date": "06/16/2020", "effective_date": "06/30/2020",
        "source_file": "warn-report-for-7-1-2019-to-6-30-2020.pdf",
    }, "EDD report row named 'Test Employer' in Sacramento (the agency's seat), "
       "no address; not a filed employer"),
    ("CA", {
        "company": "test", "city": "West Sacramento", "num_employees": "52",
        "notice_date": "07/28/2020", "effective_date": "08/31/2020",
        "source_file": "warn-report-for-7-1-2020-to-06-30-2021.pdf",
    }, "EDD report row whose employer is the word 'test', no address"),
    ("IL", {
        "IEBS Id": "20220502001", "Location Name": "ABC Manufacturing LLC",
        "Location Address": "100 W Randolph Street",
    }, "placeholder employer at 100 W Randolph St, Chicago (the State of "
       "Illinois Thompson Center); filed twice the same day under consecutive "
       "IEBS ids differing only in Causes"),
    ("IL", {
        "IEBS Id": "20220502002", "Location Name": "ABC Manufacturing LLC",
        "Location Address": "100 W Randolph Street",
    }, "second copy of the placeholder IEBS entry 20220502001"),
)


def _cell(row: dict, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def non_notice_reason(state: str, row: dict) -> str | None:
    """A hold reason for a raw row that is not a notice, else None."""
    state = state.upper()
    if state == "NC" and "WARN Notice: WARN Notice Name" in row:
        others = [_cell(row, k) for k in row if k and k != "County"]
        if all(_INTEGER.fullmatch(v.replace(",", "")) for v in others) \
                and len(_cell(row, "Warn Number")) <= 4:
            return "nc_county_summary_count_row"
    if state == "NE":
        # Rows the NE collector tagged as NDOL's layoff/closure report, not
        # its WARN report (normalize.custom.ne).
        from warnlive.normalize.custom.ne import hold_reason

        reason = hold_reason(row)
        if reason:
            return reason
    for rule_state, cells, _evidence in _TEST_RECORDS:
        if rule_state == state and all(_cell(row, k) == v for k, v in cells.items()):
            return "apparent_agency_test_record"
    return None


def employer_hold_reason(employer_name: str | None) -> str | None:
    """Hold a row whose filed employer has no letters (e.g. NJ '1961').

    Such a name identifies no employer; the row is kept for review rather
    than admitted under a number.
    """
    if employer_name and not any(ch.isalpha() for ch in employer_name):
        return "employer_name_has_no_letters_review"
    return None
