"""Distinct entries of one source document keep their own identity."""

import json

import pytest

from warnlive.normalize import entries
from warnlive.normalize.engine import _dedupe_key, _record_hash
from warnlive.store import db as db_mod
from warnlive.store.dedupe import ingest


def _rec(state="CA", employer="Abbott Vascular", location="Temecula",
         effective="2014-11-12", workers=9, **extra):
    rec = {
        "state": state, "employer_name": employer, "location": location,
        "notice_date": None, "effective_date": effective,
        "employees_affected": workers, "layoff_type": "unknown",
        "is_temporary": None, "is_amendment": 0, "source_url": None,
        "source_notice_id": None, "raw_extra": json.dumps({"w": workers}),
    }
    rec.update(extra)
    rec["dedupe_key"] = _dedupe_key(dict(rec, notice_date=rec["effective_date"]))
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


@pytest.fixture()
def conn(tmp_path):
    conn = db_mod.connect(tmp_path / "t.sqlite")
    db_mod.init_db(conn)
    return conn


def test_registry_policies_cover_ca_pa_mi_ne_only():
    assert {s for s in ("CA", "PA", "MI", "NE", "NY", "WI", "TX")
            if entries.same_key_policy(s) == "distinct_rows"} == {"CA", "PA", "MI", "NE"}


def _in_file(rec, source_file):
    rec = dict(rec, raw_extra=json.dumps({"w": rec["employees_affected"],
                                          "source_file": source_file}))
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_same_key_rows_in_two_fiscal_year_reports_are_one_notice_with_versions(conn):
    """A notice re-listed with a corrected count in the next FY report is a
    revision, not a second entry (Broadcom Irvine 2016-01-29: 689 then 771)."""
    rows = [_in_file(_rec(workers=689, effective="2016-01-29"), "fy15-16.pdf"),
            _in_file(_rec(workers=771, effective="2016-01-29"), "fy16-17.pdf")]
    out, report = entries.qualify_same_document_entries(conn, "CA", rows)
    assert [rec["dedupe_key"] for rec in out] == [rows[0]["dedupe_key"]] * 2
    assert report["qualified_rows"] == 0 and report["cross_document_key_groups"] == 1
    stats = ingest(conn, out, "2026-09-30")
    assert (stats.new, stats.updated) == (1, 1)
    [(versions, workers)] = conn.execute(
        "SELECT current_version, employees_affected FROM notices").fetchall()
    assert (versions, workers) == (2, 771)


def test_same_key_rows_in_one_report_stay_two_entries_beside_another_report(conn):
    rows = [_in_file(_rec(workers=19), "fy15-16.pdf"),
            _in_file(_rec(workers=57), "fy16-17.pdf"),
            _in_file(_rec(workers=58), "fy16-17.pdf")]
    out, report = entries.qualify_same_document_entries(conn, "CA", rows)
    keys = [rec["dedupe_key"] for rec in out]
    assert keys[0] == keys[1] == rows[0]["dedupe_key"]
    assert keys[2] == entries.entry_key(rows[0]["dedupe_key"], rows[2])
    assert report["groups"] == 1 and report["qualified_rows"] == 1


def test_source_document_uses_configured_fields_then_artifact():
    rec = _rec()
    assert entries.source_document("PA", rec) == ""
    assert entries.source_document("CA", _in_file(rec, "a.pdf")) == "source_file=a.pdf"
    annual = dict(rec, raw_extra=json.dumps({"year_file": 2014}))
    assert entries.source_document("CA", annual) == "year_file=2014"
    artifact = dict(rec, source_details=json.dumps({"source_artifact": "x/2014.pdf"}))
    assert entries.source_document("CA", artifact) == "source_artifact=x/2014.pdf"


def test_distinct_rows_get_entry_keys_and_first_keeps_legacy_key(conn):
    rows = [_rec(workers=9), _rec(workers=41), _rec(workers=53), _rec(workers=41)]
    legacy = rows[0]["dedupe_key"]
    out, report = entries.qualify_same_document_entries(conn, "CA", rows)
    keys = [rec["dedupe_key"] for rec in out]
    assert keys[0] == legacy and out[0] is rows[0]
    assert len(set(keys)) == 3 and keys[1] == keys[3]
    assert keys[1] == entries.entry_key(legacy, rows[1])
    detail = json.loads(out[1]["source_details"])["entry"]
    assert detail == {"basis": "same_document_distinct_row",
                      "discriminator_fields": list(entries.DISCRIMINATOR_FIELDS)}
    assert report["qualified_rows"] == 3 and report["qualified_keys"] == 2
    stats = ingest(conn, out, "2026-09-29")
    assert (stats.new, stats.updated, stats.coalesced) == (3, 0, 1)


def test_versions_policy_and_singletons_are_unchanged(conn):
    rows = [_rec(workers=9), _rec(workers=41)]
    assert entries.qualify_same_document_entries(conn, "CA", rows, "versions")[0] == rows
    single = [_rec(workers=9), _rec(employer="Other", workers=3)]
    assert entries.qualify_same_document_entries(conn, "CA", single)[0] == single


def test_stored_current_version_keeps_the_legacy_key(conn):
    rows = [_rec(workers=9), _rec(workers=53)]
    ingest(conn, [rows[1]], "2026-01-01")  # a released notice folded to its last row
    out, _ = entries.qualify_same_document_entries(conn, "CA", rows)
    assert out[1]["dedupe_key"] == rows[1]["dedupe_key"]
    assert out[0]["dedupe_key"] == entries.entry_key(rows[0]["dedupe_key"], rows[0])
    # Re-running over the same database and rows is stable.
    ingest(conn, out, "2026-01-02")
    again, _ = entries.qualify_same_document_entries(conn, "CA", rows)
    assert [r["dedupe_key"] for r in again] == [r["dedupe_key"] for r in out]


def test_phase_group_sums_itemized_workers_and_spans_dates():
    rows = [_rec("WI", "Hostess", "Oshkosh", "2012-07-04", 13),
            _rec("WI", "Hostess", "Oshkosh", "2012-06-01", 2)]
    folded, base = entries.fold_phase_group(rows + [rows[0]])
    assert base == rows[1]["raw_record_hash"]
    assert (folded["employees_affected"], folded["effective_date"],
            folded["effective_date_end"]) == (15, "2012-06-01", "2012-07-04")
    details = json.loads(folded["source_details"])
    assert details["effective_date_interpretation"] == "list_or_phases"
    assert [p["workers"] for p in details["phases"]] == [13, 2]
    assert folded["raw_record_hash"] == _record_hash(folded)


def test_phase_group_does_not_sum_an_original_and_its_update():
    rows = [_rec("WI", "Fleming", "X", "2003-06-10", 134),
            _rec("WI", "Fleming", "X", "2003-06-24", 144, is_amendment=1)]
    folded, _ = entries.fold_phase_group(rows)
    assert folded["employees_affected"] is None
    assert json.loads(folded["source_details"])["worker_allocation"] == "original_and_update_rows"


def _ny(control, page, closing, workers=395):
    raw = {"Control Number": control, "wayback_id": str(page)}
    return _rec("NY", "ConAgra Foods", "Chautauqua", closing, workers,
                notice_date="2014-03-20", raw_extra=json.dumps(raw))


def test_one_control_number_is_one_filing_with_versions_in_detail_order():
    rows = [_ny("2013-0291", 4522, "2015-05-24"), _ny("2013-0291", 4709, "2015-02-28"),
            _ny("2013-0291", 4977, "2015-05-24")]
    key = rows[0]["dedupe_key"]
    out, held = entries.control_number_versions(key, rows)
    assert held == [] and {r["dedupe_key"] for r in out} == {key}
    # Latest page per content leads; 4977 repeats 4522 and is the current version.
    assert [json.loads(r["raw_extra"])["wayback_id"] for r in out] == ["4709", "4977", "4522"]


def test_distinct_control_numbers_are_distinct_filings_and_missing_is_held():
    rows = [_ny("2013-0306", 1, "2014-06-27", None), _ny("2013-0309", 2, "2014-06-27", 990)]
    key = rows[0]["dedupe_key"]
    out, held = entries.control_number_versions(key, rows)
    assert held == [] and len({r["dedupe_key"] for r in out}) == 2 and key not in {
        r["dedupe_key"] for r in out}
    missing = [_ny("", 1, "2014-06-27", 1), _ny("2013-0309", 2, "2014-06-27", 990)]
    assert entries.control_number_versions(key, missing) == ([], missing)


def _wi(received, amendment, effective="2005-06-30", workers=220):
    details = {"agency_received_date": received, "legacy_notice_key_date": received}
    rec = _rec("WI", "SJP", "New London", effective, workers, is_amendment=amendment,
               source_details=json.dumps(details, sort_keys=True))
    rec["dedupe_key"] = _dedupe_key(rec)
    rec["raw_record_hash"] = _record_hash(rec)
    return rec


def test_identical_update_rows_become_versions_of_the_earlier_notice(conn):
    original, update, later = _wi("2005-01-19", 0), _wi("2005-02-03", 1), _wi("2005-03-14", 1)
    other = _wi("2005-01-01", 0, workers=5)
    out, report = entries.fold_identical_updates("WI", [update, other, later, original])
    assert report["folded_rows"] == 2
    assert [r["dedupe_key"] for r in out] == [other["dedupe_key"]] + [original["dedupe_key"]] * 3
    assert json.loads(out[2]["source_details"])["version_of"]["retired_dedupe_key"] == update["dedupe_key"]
    stats = ingest(conn, out, "2026-09-29")
    assert (stats.new, stats.updated) == (2, 2)


def test_update_folding_leaves_originals_contentless_rows_and_other_states():
    first, second = _wi("2002-10-31", 0), _wi("2002-11-05", 0)
    assert entries.fold_identical_updates("WI", [first, second])[0] == [first, second]
    blank = [_wi("2011-06-16", 1, None, None), _wi("2011-06-23", 1, None, None)]
    out, report = entries.fold_identical_updates("WI", blank)
    assert out == blank and report["skipped_contentless_updates"] == 1
    assert entries.fold_identical_updates("CA", [first, _wi("2003-01-01", 1)])[1]["folded_rows"] == 0
