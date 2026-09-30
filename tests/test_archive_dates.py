"""Archive projections blank dates outside the live transformer's window."""

import json
from datetime import date

import pytest

from warnlive.migrate import archive_dates
from warnlive.normalize.engine import _record_hash


def _manifest(tmp_path, **extra):
    manifest = {"artifacts": [
        {"file": "sheet.xlsx", "retrieved_live_utc": "2026-09-30T14:50:05Z"},
        {"file": "log-20161227190458.pdf", "capture_utc": "2016-12-27T19:04:58Z"},
    ], **extra}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def _record(artifact, **fields):
    rec = {
        "state": "KY", "employer_name": "Cenveo", "location": "Shelby",
        "notice_date": None, "effective_date": None, "employees_affected": 5,
        "layoff_type": "closure", "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.test", "source_notice_id": None,
        "source_details": json.dumps({
            "source_artifact": f"agency/ky_archive/{artifact}",
            "source_row": f"agency/ky_archive/{artifact}:row:10",
            "raw_fields": {"Projected Dates": "2041-06-04T00:00:00"},
        }),
        "dedupe_key": "k",
    }
    rec.update(fields)
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_window_uses_state_minimum_year_and_capture_anchor():
    assert archive_dates.window("KY", date(2026, 9, 30)) == (date(1997, 1, 1), date(2027, 9, 30))
    assert archive_dates.window("IA", date(2016, 12, 27)) == (date(1988, 1, 1), date(2017, 12, 27))


def test_far_future_projected_date_is_blanked_with_note_and_raw_cell_kept(tmp_path):
    directory = _manifest(tmp_path)
    rec = _record("sheet.xlsx", effective_date="2041-06-04",
                  effective_date_precision="day", effective_date_basis="reported")
    details = json.loads(rec["source_details"])
    details.update(projected_action_date="2041-06-04", agency_received_date="2014-04-04")
    rec["source_details"] = json.dumps(details)
    before = rec["raw_record_hash"]

    blanked = archive_dates.apply([rec], "KY", directory)

    assert rec["effective_date"] is None
    assert rec["effective_date_precision"] is None and rec["effective_date_basis"] is None
    details = json.loads(rec["source_details"])
    assert "projected_action_date" not in details
    assert details["agency_received_date"] == "2014-04-04"
    assert details["raw_fields"] == {"Projected Dates": "2041-06-04T00:00:00"}
    assert [n["field"] for n in details["parse_notes"]] == [
        "effective_date", "source_details.projected_action_date"]
    assert {n["reason"] for n in details["parse_notes"]} == {"implausible_date_blanked"}
    assert details["parse_notes"][0]["window"] == ["1997-01-01", "2027-09-30"]
    assert rec["raw_record_hash"] != before and rec["raw_record_hash"] == _record_hash(rec)
    assert [(b["state"], b["field"], b["value"]) for b in blanked] == [
        ("KY", "effective_date", "2041-06-04"),
        ("KY", "source_details.projected_action_date", "2041-06-04")]


def test_upper_bound_follows_the_artifacts_own_capture(tmp_path):
    directory = _manifest(tmp_path)
    old = _record("log-20161227190458.pdf", notice_date="2006-08-18", effective_date="2018-01-02")
    fine = _record("log-20161227190458.pdf", notice_date="2006-08-18", effective_date="2017-12-27")
    early = _record("log-20161227190458.pdf", notice_date="2006-08-18", effective_date="1969-10-20")

    blanked = archive_dates.apply([old, fine, early], "IA", directory)

    assert old["effective_date"] is None and early["effective_date"] is None
    assert fine["effective_date"] == "2017-12-27"
    assert old["notice_date"] == "2006-08-18"
    assert [b["value"] for b in blanked] == ["2018-01-02", "1969-10-20"]


def test_in_window_records_are_untouched(tmp_path):
    directory = _manifest(tmp_path)
    rec = _record("sheet.xlsx", effective_date="2014-06-04")
    snapshot = dict(rec)
    assert archive_dates.apply([rec], "KY", directory) == []
    assert rec == snapshot


def test_nested_amendment_dates_are_checked(tmp_path):
    directory = _manifest(tmp_path)
    rec = _record("sheet.xlsx")
    details = json.loads(rec["source_details"])
    details["amendments"] = [{"amendment_notice_date": "2009-01-01", "reported_layoff_date": "1901-01-01"}]
    rec["source_details"] = json.dumps(details)
    blanked = archive_dates.apply([rec], "IA", directory)
    assert [b["field"] for b in blanked] == ["source_details.amendments[0].reported_layoff_date"]
    assert json.loads(rec["source_details"])["amendments"] == [{"amendment_notice_date": "2009-01-01"}]


def test_portal_capture_and_missing_capture(tmp_path):
    directory = _manifest(tmp_path, capture={"capture_end_utc": "2026-09-30T15:26:40Z"})
    rec = _record("unlisted.html", effective_date="2028-01-01")
    rec["source_details"] = json.dumps({"source_row": "r1"})
    assert [b["value"] for b in archive_dates.apply([rec], "AZ", directory)] == ["2028-01-01"]

    (tmp_path / "manifest.json").write_text(json.dumps({"artifacts": []}))
    with pytest.raises(ValueError):
        archive_dates.apply([_record("unlisted.html")], "KY", tmp_path)
    assert archive_dates.apply([_record("unlisted.html")], "KY", tmp_path,
                               fallback=date(2026, 9, 30)) == []
