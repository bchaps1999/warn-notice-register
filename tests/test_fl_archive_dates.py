"""Florida archive parsing for single layoff dates and explicit ranges."""

import json

from warnlive.backfill import state_archives


def test_fl_layoff_thru_range_populates_ordered_start_and_end(tmp_path, monkeypatch):
    html = b"""<table>
      <tr><th>COMPANY NAME</th><th>NOTICE DATE</th><th>LAYOFF DATE</th>
          <th>EMPLOYEES AFFECTED</th><th>INDUSTRY</th></tr>
      <tr><td>Combustion Tec</td><td>8/10/1999</td>
          <td>9/10/1999 thru 10/29/1999</td><td>12</td><td>Manufacturing</td></tr>
    </table>"""
    monkeypatch.setattr(state_archives, "FL_YEARS", [1999])
    monkeypatch.setattr(state_archives, "_download", lambda _url, _dest: html)

    [record] = state_archives.fetch_fl(tmp_path)

    assert record["effective_date"] == "1999-09-10"
    assert record["effective_date_end"] == "1999-10-29"
    assert (record["notice_date_precision"], record["notice_date_basis"]) == ("day", "reported")
    assert (record["effective_date_precision"], record["effective_date_basis"]) == ("day", "reported")
    assert (record["effective_date_end_precision"], record["effective_date_end_basis"]) == ("day", "reported")
    assert json.loads(record["source_details"])["date_evidence_rule"] == "fl_archive_html_notice_layoff_v1"
    assert json.loads(record["raw_extra"])["LAYOFF DATE"] == (
        "9/10/1999 thru 10/29/1999"
    )


def test_fl_reversed_thru_pair_is_left_for_review():
    assert state_archives._fl_effective_dates("10/29/1999 thru 9/10/1999") == (
        None,
        None,
    )


def test_fl_reversed_source_range_has_no_supported_effective_endpoint(tmp_path, monkeypatch):
    html = b"""<table><tr><th>COMPANY NAME</th><th>NOTICE DATE</th><th>LAYOFF DATE</th>
      <th>EMPLOYEES AFFECTED</th><th>INDUSTRY</th></tr><tr><td>Employer</td><td>8/10/1999</td>
      <td>10/29/1999 thru 9/10/1999</td><td>12</td><td>Other</td></tr></table>"""
    monkeypatch.setattr(state_archives, "FL_YEARS", [1999])
    monkeypatch.setattr(state_archives, "_download", lambda _url, _dest: html)
    [record] = state_archives.fetch_fl(tmp_path)
    assert record["effective_date"] is None and record["effective_date_end"] is None
    assert record["effective_date_precision"] is None
    assert record["effective_date_end_precision"] is None
    assert json.loads(record["source_details"])["effective_date_end_status"] == "before_start_review"


def test_fl_swapped_date_headers_fail_closed(tmp_path, monkeypatch):
    html = b"""<table><tr><th>COMPANY NAME</th><th>LAYOFF DATE</th><th>NOTICE DATE</th>
      <th>EMPLOYEES AFFECTED</th><th>INDUSTRY</th></tr><tr><td>Employer</td><td>9/10/1999</td>
      <td>8/10/1999</td><td>12</td><td>Other</td></tr></table>"""
    monkeypatch.setattr(state_archives, "FL_YEARS", [1999])
    monkeypatch.setattr(state_archives, "_download", lambda _url, _dest: html)
    import pytest
    with pytest.raises(ValueError, match="date column order changed"):
        state_archives.fetch_fl(tmp_path)


def test_fl_single_layoff_date_stays_a_point_date():
    assert state_archives._fl_effective_dates("9/10/1999") == (
        "1999-09-10",
        None,
    )
