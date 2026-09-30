"""Mississippi: words spilled into a date cell go back to the next cell.

Cells are the parser's output for warn-py2025-qtr-4-apr-jun-2026.pdf
(workdir/raw/ms.csv rows 147-148, captured 2026-07-26).
"""

from __future__ import annotations

import csv
import json

from warnlive.normalize.custom.ms import date_spill, filed_company
from warnlive.normalize.engine import _dedupe_key, normalize_file

HEADER = ["date_notice", "company", "workforce_area", "event_number", "naics",
          "action_type", "affected", "date_effective", "reason", "blank_entry",
          "city", "county"]
ROWS = [
    ["4/17/2026 Aramark", "Services, Inc", "Delta", "RR-MS- 2025-0020",
     "722310- Food Services Contractor", "Closure", "59", "6/15/2026 WARN – Due",
     "Businesses Circumstances/in negotiations with buyer", "", "Greenwood", "Leflore"],
    ["05/11/2026 Leggett &", "Platt Flooring Products", "MS Partnership",
     "RR-MS- 2025-0021", "313230- Non- Woven Fabric Mills", "Closure", "86",
     "6/11/2026 WARN-Plant", "Consolidation", "", "Houston", "Chickasaw"],
    # Older layout: the company cell ends in "City (County)".
    ["03/23/2023 Sun Air Products", "Belmont (Tishomingo)", "", "", "", "Closure", "40",
     "05/23/2023", "", "", "", ""],
    # Two dates are not a spill.
    ["08/31/2023", "Example Co", "", "", "", "Layoff", "5", "08/31/2023 09/01/2023",
     "", "", "", "Hinds"],
]


def _normalize(tmp_path):
    with open(tmp_path / "ms.csv", "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(ROWS)
    return normalize_file("ms", tmp_path, None, observed_at="2026-09-30")


def test_date_spill_reads_the_date_and_keeps_the_words():
    assert date_spill("4/17/2026 Aramark") == ("4/17/2026", "Aramark")
    assert date_spill("05/11/2026  Leggett &") == ("05/11/2026", "Leggett &")
    assert date_spill("08/31/2023 09/01/2023") is None
    assert date_spill("4/17/2026") is None
    assert filed_company({"date_notice": "05/11/2026 Leggett &",
                          "company": "Platt Flooring Products"}) == (
        "Leggett & Platt Flooring Products")


def test_spilled_rows_get_their_filed_employer_and_dates(tmp_path):
    aramark, leggett, sun_air, example = _normalize(tmp_path).records
    assert (aramark["employer_name"], aramark["notice_date"], aramark["effective_date"],
            aramark["location"]) == ("Aramark Services, Inc", "2026-04-17", "2026-06-15",
                                     "Leflore")
    assert (leggett["employer_name"], leggett["notice_date"], leggett["effective_date"]) == (
        "Leggett & Platt Flooring Products", "2026-05-11", "2026-06-11")
    assert (sun_air["employer_name"], sun_air["location"], sun_air["notice_date"]) == (
        "Sun Air Products", "Belmont (Tishomingo)", "2023-03-23")
    assert example["employer_name"] == "Example Co"
    raw = json.loads(aramark["raw_extra"])
    assert raw["date_notice"] == "4/17/2026 Aramark" and raw["company"] == "Services, Inc"


def test_spilled_rows_keep_the_key_of_their_filed_cells(tmp_path):
    """Needs the MS spill hook in normalize.details."""
    aramark, leggett, sun_air, _ = _normalize(tmp_path).records
    details = json.loads(aramark["source_details"])
    assert details["date_cell_spill"]["fields"]["date_notice"] == {
        "source_text": "4/17/2026 Aramark", "date_text": "4/17/2026", "spill_to": "company"}
    assert details["date_cell_spill"]["fields"]["date_effective"]["spill_to"] == "reason"
    assert aramark["dedupe_key"] == _dedupe_key({
        "state": "MS", "employer_name": "Services, Inc", "notice_date": "2026-04-17",
        "location": "Leflore"})
    assert leggett["dedupe_key"] == _dedupe_key({
        "state": "MS", "employer_name": "Platt Flooring Products",
        "notice_date": "2026-05-11", "location": "Chickasaw"})
    assert sun_air["dedupe_key"] == _dedupe_key({
        "state": "MS", "employer_name": "Belmont (Tishomingo)",
        "notice_date": "2023-03-23", "location": None})
