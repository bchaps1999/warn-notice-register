"""Only labeled, matching source cells establish canonical day precision."""

import json

import pytest

from warnlive.backfill import state_archives
from warnlive.normalize.details import extract


def test_labeled_notice_and_action_days_are_detected_independently():
    raw = {"Company": "Example", "Notice Date": "1/7/2010",
           "Effective Date": "3/9/2010"}
    result = extract("MD", raw, {"notice_date": "2010-01-07",
                                 "effective_date": "2010-03-09"})
    assert (result["notice_date_precision"], result["notice_date_basis"]) == ("day", "reported")
    assert (result["effective_date_precision"], result["effective_date_basis"]) == ("day", "reported")
    evidence = json.loads(result["source_details"])["date_precision_evidence"]
    assert evidence["notice_date"]["source_field"] == "Notice Date"
    assert evidence["effective_date"]["matched_day"] == "2010-03-09"


def test_mismatch_and_multi_date_cell_cannot_gain_precision():
    result = extract("MA", {"DATE(S) OF LAYOFFS": "5/31/22-11/18/22"},
                     {"effective_date": "2022-05-31"})
    assert result.get("effective_date_precision") is None
    result = extract("MD", {"Notice Date": "1/7/2010", "Effective Date": "3/9/2010"},
                     {"notice_date": "2010-01-08", "effective_date": "2010-03-09"})
    assert result.get("notice_date_precision") is None
    assert result["effective_date_precision"] == "day"


def test_receipt_date_is_not_legal_notice_precision():
    result = extract("WA", {"Received Date": "7/8/2026",
                             "Layoff Start Date": "8/7/2026"},
                     {"notice_date": "2026-07-08", "effective_date": "2026-08-07"})
    assert result["notice_date"] is None
    assert result.get("notice_date_precision") is None
    assert result["effective_date_precision"] == "day"
    details = json.loads(result["source_details"])
    assert details["date_evidence_rule"] == "wa_agency_received_role_v1"
    assert details["date_precision_evidence"]["effective_date"]["source_field"] == "Layoff Start Date"


def test_california_modern_shape_and_exact_source_match_required():
    raw = {"company": "Example", "num_employees": "65",
           "source_file": "warnreportfor7-1-2014to06-30-2015.pdf",
           "received_date": "03/09/2015", "notice_date": "01/15/2014",
           "effective_date": "03/16/2015"}
    rec = {"notice_date": "2014-01-15", "effective_date": "2015-03-16"}
    assert extract("CA", raw, rec)["notice_date_precision"] == "day"
    assert extract("CA", raw, rec)["effective_date_precision"] == "day"
    assert "notice_date_precision" not in extract("CA", {k: v for k, v in raw.items()
                                                       if k != "source_file"}, rec)
    assert "notice_date_precision" not in extract("CA", raw, dict(rec, notice_date="2014-01-16"))


def test_range_end_gets_own_precision_and_existing_evidence_is_preserved():
    result = extract("CO", {"begin_date": "8/10/26", "end_date": "8/24/26"},
                     {"effective_date": "2026-08-10",
                      "effective_date_end": "2026-08-24",
                      "effective_date_precision": "month"})
    assert result.get("effective_date_precision") is None
    assert result["effective_date_end_precision"] == "day"


def test_california_archive_requires_labeled_pdf_columns(tmp_path, monkeypatch):
    class Table:
        def __init__(self, rows):
            self.rows = rows

        def extract(self):
            return self.rows

    class Page:
        def __init__(self, rows):
            self.rows = rows

        def find_tables(self):
            return [Table(self.rows)]

    class Pdf:
        def __init__(self, rows):
            self.pages = [Page(rows)]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    rows = [["Company Name", "Location", "Employees Affected", "Layoff Date"],
            ["Example", "Oakland", "20", "4/1/2000"]]
    monkeypatch.setattr(state_archives, "CA_YEARS", [2000])
    monkeypatch.setattr(state_archives, "_wayback_captures", lambda _url: ["https://example.gov/a.pdf"])
    monkeypatch.setattr(state_archives, "_download", lambda _url, _dest: b"%PDF test")
    monkeypatch.setattr("pdfplumber.open", lambda _path: Pdf(rows))
    [record] = state_archives.fetch_ca(tmp_path)
    assert (record["effective_date_precision"], record["effective_date_basis"]) == ("day", "reported")
    assert json.loads(record["source_details"])["date_precision_evidence"]["effective_date"]["source_text"] == "4/1/2000"

    rows[0] = ["Company(cid:10)Name", "Location", "EmployeesAffected", "Layoff\nDate"]
    assert state_archives.fetch_ca(tmp_path)[0]["effective_date_precision"] == "day"

    rows[0] = ["Company Name", "Location", "Layoff Date", "Employees Affected"]
    with pytest.raises(ValueError, match="column order changed"):
        state_archives.fetch_ca(tmp_path)
