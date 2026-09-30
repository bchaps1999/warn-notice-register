"""Source observations remain an auditable input ledger without model results."""

import pytest

from warnlive.store import db
from warnlive.store.observations import store_observations


def _row(pointer="agency/la/2025.pdf:p1:r1"):
    return {
        "source_artifact": "agency/la/2025.pdf",
        "source_row": pointer,
        "source_row_sha256": "row-hash",
        "kind": "notice",
        "company_text": "Example",
        "raw_cells": ["Example"],
    }


def test_observations_record_exclusion_and_replay_without_model_columns(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    row = _row()
    admission = {row["source_row"]: ("event_unresolved", None)}
    first = store_observations(conn, [row], admission, "bundle")
    second = store_observations(conn, [row], admission, "bundle")
    assert first == second
    assert first["by_admission_status"]["event_unresolved"] == 1
    saved = conn.execute("SELECT * FROM source_observations").fetchall()
    assert len(saved) == 1
    assert saved[0]["notice_id"] is None
    assert '"employer":"Example"' in saved[0]["deterministic_json"]
    assert "cleaned_json" not in saved[0].keys()


def test_observation_store_rejects_missing_disposition_atomically(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    with pytest.raises(ValueError, match="match exactly once"):
        store_observations(conn, [_row()], {}, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_observation_store_rolls_back_prior_rows_on_invalid_admission(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    first = _row()
    second = _row("agency/la/2025.pdf:p1:r2")
    with pytest.raises(ValueError, match="only admitted observations"):
        store_observations(conn, [first, second], {
            first["source_row"]: ("event_unresolved", None),
            second["source_row"]: ("admitted", None),
        }, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0


def test_observation_store_rejects_changed_source_on_replay(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    first = _row()
    admission = {first["source_row"]: ("event_unresolved", None)}
    store_observations(conn, [first], admission, "bundle")
    changed = {**first, "company_text": "Different"}
    with pytest.raises(ValueError, match="stored source observation changed"):
        store_observations(conn, [changed], admission, "bundle")
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 1


def test_kentucky_observation_keeps_out_of_state_row_separate(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    row = {
        "source_artifact": "agency/ky/WARN-Report-2026-09-16.csv",
        "source_row": "agency/ky/WARN-Report-2026-09-16.csv:row:4",
        "source_row_sha256": "row-hash",
        "kind": "notice",
        "company_text": "Example",
        "raw": {"County": "Out of the State County"},
    }
    store_observations(conn, [row], {row["source_row"]: ("identity_unresolved", None)}, "bundle")
    saved = conn.execute(
        "SELECT state, admission_status, notice_id, raw_json FROM source_observations"
    ).fetchone()
    assert (saved["state"], saved["admission_status"], saved["notice_id"]) == (
        "KY", "identity_unresolved", None,
    )
    assert "Out of the State County" in saved["raw_json"]


def test_missouri_rapid_response_rows_are_kept_as_unresolved_observations(tmp_path):
    from warnlive.migrate.offline_rebuild import _mo_observations

    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    held = {
        "origin": "agency/mo_historical/WARN_Data1997-2018.xlsx", "state": "MO",
        "reason": "unreviewed_rapid_response_row",
        "source_row": "agency/mo_historical/WARN_Data1997-2018.xlsx:sha256:x:sheet:S:row:58",
        "source_row_sha256": "row-hash",
        "raw_extra": '{"# Affected": 50, "Address": "A Ave", "Company Name": "Airport", '
                     '"Date Rec\'d": "2019-03-11T00:00:00", '
                     '"Layoff or Closing Date": "2019-03-31T00:00:00", "Location(s)": "St. Joseph"}',
    }
    [(row, key)] = _mo_observations([], [held])
    assert key is None
    assert (row["agency_received_date"], row["effective_date"], row["workers_reported"]) == (
        "2019-03-11", "2019-03-31", 50)
    assert "notice_date" not in row
    store_observations(conn, [row], {row["source_row"]: ("identity_unresolved", None)}, "bundle")
    saved = conn.execute("SELECT state, admission_status FROM source_observations").fetchone()
    assert tuple(saved) == ("MO", "identity_unresolved")


def test_nebraska_layoff_closure_rows_are_observations_outside_the_warn_report(tmp_path):
    import json

    from warnlive.migrate.offline_rebuild import _ne_observations

    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    raw = {"Date": "10/14/2014", "Company": "Nationstar", "Type": "Layoff",
           "Jobs Affected": "78", "City": "Scottsbluff", "Location": "Scottsbluff",
           "source_report": "layoff_closure_report",
           "ndol_source_page": "https://dol.nebraska.gov/LayoffServices/"
                               "LayoffAndClosureReportData/?year=2014",
           "ndol_page_row": "10", "ndol_notice_link": "",
           "ndol_matched_warn_row": "warn_report:2014:3"}
    exceptions = [{"origin": "raw/ne.csv", "reason": "ne_layoff_closure_report_not_warn",
                   "prepared_row": 5, "source_row_sha256": "row-hash",
                   "raw_extra": json.dumps(raw)},
                  {"origin": "raw/ne.csv", "reason": "parse_failure", "raw_extra": "{}"}]
    [row] = _ne_observations(exceptions)
    assert row["source_artifact"] == "backfill/cache/archives/ne/layoff_closure_report-2014.html"
    assert row["source_row"] == row["source_artifact"] + "#row10"
    assert (row["workers_reported"], row["ndol_matched_warn_row"]) == (78, "warn_report:2014:3")
    assert "notice_date" not in row
    store_observations(conn, [row], {row["source_row"]: ("not_in_agency_warn_report", None)},
                       "bundle")
    saved = conn.execute("SELECT state, admission_status, notice_id FROM source_observations").fetchone()
    assert tuple(saved) == ("NE", "not_in_agency_warn_report", None)


def test_observation_pointers_must_name_bundle_members():
    from warnlive.migrate.offline_rebuild import _check_observation_artifacts

    rows = [{"source_artifact": "backfill/cache/archives/ne/warn_report-2014.html"},
            {"source_artifact": "agency/la/2025.pdf"}]
    _check_observation_artifacts(rows, {"agency/la/2025.pdf",
                                        "backfill/cache/archives/ne/warn_report-2014.html"})
    with pytest.raises(ValueError, match="not in the bundle"):
        _check_observation_artifacts(rows, {"agency/la/2025.pdf"})


def test_observation_store_rejects_unknown_artifact_paths(tmp_path):
    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    for artifact in ("agency/ne/layoff_closure_report-2014.html",
                     "backfill/cache/archives/wi/dwd-2016.htm", "cache/archives/ne/x.html"):
        row = {"source_artifact": artifact, "source_row": artifact + "#row1",
               "source_row_sha256": "h", "kind": "notice"}
        with pytest.raises(ValueError, match="unexpected official source artifact"):
            store_observations(conn, [row], {row["source_row"]: ("identity_unresolved", None)},
                               "bundle")
