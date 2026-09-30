"""Pinned annual agency inputs keep source rows and admission decisions auditable."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.ga_archive_source import project as project_ga
from warnlive.migrate.ny_annual_source import project as project_ny, read_artifacts
from warnlive.migrate.ny_overlay import _employer_group
from warnlive.normalize.engine import _record_hash


ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots"
NY = ROOT / "ny/annual_2006_2026"
GA = ROOT / "ga/archives-2018-2022"


def test_ny_annual_source_accounts_for_each_observation():
    rows = read_artifacts(NY)
    records, held, report = project_ny(NY)
    assert len(rows) == 9044
    assert len({row["source_row_id"] for row in rows}) == len(rows)
    assert (len(records), len(held)) == (report["admitted"], report["held"])
    assert len(records) + len(held) == len(rows)
    assert all(record["source_identity"].startswith("NY:dashboard-observation:") for record in records)


def test_ny_annual_source_uses_event_identity_not_employer_alone():
    records, _, _ = project_ny(NY)
    example = records[0]
    named, _, _ = project_ny(NY, {example["employer_name"]})
    assert len(named) == len(records)
    reduced, held, _ = project_ny(
        NY, {example["employer_name"]},
        {(example["location"].casefold(), example["effective_date"], example["employees_affected"])},
    )
    assert len(reduced) == len(records) - 1
    assert any(row["reason"] == "existing_site_action_worker_identity_unresolved" for row in held)
    assert all(row["disposition"] != "notice" for row in held)
    reduced, held, _ = project_ny(
        NY, existing_events={(_employer_group(example["employer_name"]),
                              example["location"].casefold(), example["effective_date"])},
    )
    assert len(reduced) == len(records) - 1
    assert any(row["reason"] == "existing_employer_site_action_identity_unresolved" for row in held)
    reduced, held, _ = project_ny(
        NY, existing_site_actions={
            (example["location"].casefold(), example["effective_date"], example["employees_affected"])
        },
    )
    assert len(reduced) == len(records) - 1
    assert any(row["reason"] == "existing_site_action_worker_identity_unresolved" for row in held)


def test_ny_repeated_employers_and_optional_fields_are_admitted_with_revision_queue():
    records, held, report = project_ny(NY)
    assert report["admitted"] > 5000
    assert report["admitted_missing_action_date"] > 500
    assert report["admitted_missing_workers"] > 100
    assert report["hold_reasons"]["possible_revision_same_event"] > 0
    assert report["admitted_filing_group_rows"] > 2000
    assert 0 < report["held_multi_site_rows"] < 200
    assert all(row["disposition"] == "unresolved" for row in held)
    assert all(row["filing_group_sites"] > 1 and row["filing_group_rows"] >= row["filing_group_sites"]
               for row in held if row["reason"] == "multi_site_filing_identity_unresolved")
    assert all(json.loads(row["source_details"])["disposition"] == "notice" for row in records)


def test_ny_distinct_site_filing_groups_are_admitted_per_listed_site():
    records, held, _ = project_ny(NY)
    grouped: dict[str, list[dict]] = {}
    for row in records:
        group = json.loads(row["source_details"]).get("filing_group")
        if group:
            grouped.setdefault(group["id"], []).append(row)
    largest = max(grouped.values(), key=len)
    assert len(largest) > 100
    # Each listed site is its own entry: its own address, key and count.
    assert len({row["location"].casefold() for row in largest}) == len(largest)
    assert len({row["dedupe_key"] for row in largest}) == len(largest)
    assert all(json.loads(row["source_details"])["filing_group"]["rows"] >= len(members)
               for members in grouped.values() for row in members)
    # A group that repeats an address stays held as a whole.
    fanout = [row for row in held if row["reason"] == "multi_site_filing_identity_unresolved"]
    assert fanout
    assert all(row["disposition_evidence"] == "same_employer_notice_multiple_sites_or_phases"
               for row in fanout)
    for row in fanout:
        assert row["filing_group_rows"] > row["filing_group_sites"] or any(
            not json.loads(other["raw_extra"])["address"].strip() for other in fanout
            if json.loads(other["raw_extra"])["company"] == json.loads(row["raw_extra"])["company"])


def test_ny_dashboard_type_cells_map_exact_values_only():
    records, _, _ = project_ny(NY)
    for row in records:
        cells = json.loads(row["source_details"])["raw_cells"]
        assert row["layoff_type"] == {"Closure": "closure", "Layoff": "mass_layoff"}.get(
            cells[6].strip(), "unknown")
        assert row["is_temporary"] == {"Permanent": 0, "Temporary": 1}.get(cells[7].strip())
    assert {row["layoff_type"] for row in records} == {"closure", "mass_layoff", "unknown"}
    assert {row["is_temporary"] for row in records} == {0, 1, None}


def test_ny_historical_id_correspondence_survives_different_site_granularity():
    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"]
                   and "filing_group" not in json.loads(row["source_details"]))
    county = json.loads(example["source_details"])["raw_cells"][5]
    prior = {"id": 42, "employer_name": example["employer_name"],
             "location": county, "notice_date": example["notice_date"],
             "effective_date": example["effective_date"],
             "employees_affected": example["employees_affected"],
             "source_notice_id": "2008-W070"}
    reduced, held, report = project_ny(NY, existing_notices=[prior])
    assert len(reduced) < len(records)
    correspondence = [row for row in held if row["reason"] ==
                      "existing_notice_event_correspondence_unresolved"]
    assert correspondence
    assert all("2008-W070" in row["existing_candidate_ids"] for row in correspondence)
    assert report["held_existing_notice_correspondence"] == len(correspondence)
    # Without the notice's key the exact match cannot be versioned: held.
    assert any(row["disposition_evidence"] == "same_employer_notice_date_workers_county"
               for row in correspondence)
    assert report["correspondence_versions"] == 0


def test_ny_exact_correspondence_becomes_a_version_of_the_control_number_notice():
    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"]
                   and "filing_group" not in json.loads(row["source_details"]))
    county = json.loads(example["source_details"])["raw_cells"][5]
    prior = {"id": 42, "dedupe_key": "k" * 40, "state": "NY",
             "employer_name": example["employer_name"].upper(),
             "location": county, "notice_date": example["notice_date"],
             "effective_date": None, "employees_affected": example["employees_affected"],
             "layoff_type": "closure", "is_temporary": None, "is_amendment": 0,
             "source_url": "https://labor.ny.gov/app/warn/details.asp?id=1",
             "source_notice_id": "2008-W070", "source_details": None}
    reduced, held, report = project_ny(NY, existing_notices=[prior])
    assert report["correspondence_versions"] == 1
    assert report["admitted"] == len(reduced) - 1
    assert len(reduced) + len(held) == len(read_artifacts(NY))
    version = next(row for row in reduced if row["dedupe_key"] == prior["dedupe_key"])
    assert version["source_notice_id"] == "2008-W070"
    assert version["employer_name"] == prior["employer_name"]
    assert version["location"] == county
    assert version["layoff_type"] == "closure"
    assert version["effective_date"] == example["effective_date"]
    assert version["site_address"] == example["location"]
    evidence = json.loads(version["source_details"])["ny_dashboard_correspondence"]
    assert evidence["basis"] == "same_employer_notice_date_workers_county"
    assert evidence["action_date_added"] is True
    assert example["dedupe_key"] not in {row["dedupe_key"] for row in reduced}
    # A notice that already has a different action date keeps it.
    dated = {**prior, "effective_date": "1999-01-01"}
    reduced, _, _ = project_ny(NY, existing_notices=[dated])
    version = next(row for row in reduced if row["dedupe_key"] == prior["dedupe_key"])
    assert version["effective_date"] == "1999-01-01"
    assert json.loads(version["source_details"])["ny_dashboard_correspondence"][
        "reported_action_date"] == example["effective_date"]


def test_ny_annual_source_rejects_worker_cell_disagreement(tmp_path):
    shutil.copytree(NY, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "ny_warn_2006.csv"
    import csv
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    rows[1][11] = "999999"
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    payload = path.read_bytes()
    manifest["artifacts"][0].update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="worker/layout mismatch"):
        read_artifacts(tmp_path)


def test_ga_archive_accounts_for_published_ids_and_holds_bad_cells():
    records, held, report = project_ga(GA)
    assert report["source_rows"] == 789
    assert len(records) + len(held) == 789
    assert len({row["source_notice_id"] for row in records}) == len(records)
    assert report["hold_reasons"]["pdf_table_text_unresolved"] == 33
    assert report["hold_reasons"]["pdf_table_row_unresolved"] == 32
    assert all(row["source_identity"] == "GA:" + row["source_notice_id"] for row in records)


def test_ga_archive_rejects_changed_pdf_bytes(tmp_path):
    shutil.copytree(GA, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "2018-ga-warn-filing-report.pdf"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        project_ga(tmp_path)


def test_ny_inexact_match_to_recovered_control_number_filing_keeps_the_dashboard_notice():
    """A loose name/date match to a filing rebuilt from conflicting detail pages
    does not withdraw the dashboard row's own notice; the match is recorded.
    The same loose match to an ordinary notice still holds the row."""
    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"]
                   and "filing_group" not in json.loads(row["source_details"]))
    base = {"id": 42, "dedupe_key": "k" * 40, "state": "NY",
            "employer_name": example["employer_name"], "location": "Elsewhere County",
            "notice_date": example["notice_date"], "effective_date": None,
            "employees_affected": None, "source_notice_id": "2008-W070"}
    for details in ({"version_order": {"basis": "agency_detail_id_order"}},
                    {"entry": {"basis": "distinct_control_number", "control_number": "2008-W070"}}):
        prior = {**base, "source_details": json.dumps(details)}
        kept, held, report = project_ny(NY, existing_notices=[prior])
        row = next(r for r in kept if r["dedupe_key"] == example["dedupe_key"])
        possible = json.loads(row["source_details"])["possible_correspondence"]
        assert possible["basis"] == "same_employer_notice_or_action_date"
        assert possible["status"] == "unreviewed_not_merged"
        assert possible["candidates"] == [{
            "dedupe_key": "k" * 40, "source_notice_id": "2008-W070",
            "fields_matched": ["employer_group", "notice_date"],
            "disposition": "held_possible_duplicate_of_admitted_notice"}]
        assert list(report["held_recovered_filings"]) == ["k" * 40]
        assert report["correspondence_versions"] == 0
        assert report["admitted_with_possible_correspondence"] >= 1
        assert len(kept) + len(held) == len(read_artifacts(NY))
    ordinary = {**base, "source_details": None}
    reduced, held, _ = project_ny(NY, existing_notices=[ordinary])
    assert example["dedupe_key"] not in {r["dedupe_key"] for r in reduced}
    assert any(r["reason"] == "existing_notice_event_correspondence_unresolved"
               and r["source_row"] == example["source_notice_id"] for r in held)


def test_recovered_filing_matching_an_admitted_dashboard_row_is_held_and_both_sides_agree(tmp_path):
    """The dashboard notice stays the single record; the recovered filing it
    loosely matches is withdrawn, and each side names the other."""
    from warnlive.migrate.ny_annual_source import HELD_RECOVERED_REASON, withdraw_held_filings
    from warnlive.store import db
    from warnlive.store.dedupe import ingest

    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"]
                   and "filing_group" not in json.loads(row["source_details"]))
    conn = db.connect(tmp_path / "c.sqlite")
    db.init_db(conn)
    filing = {"state": "NY", "employer_name": example["employer_name"],
              "location": "Elsewhere County", "notice_date": example["notice_date"],
              "effective_date": None, "employees_affected": 7, "layoff_type": "unknown",
              "is_temporary": None, "is_amendment": 0, "source_url": "https://x/details.asp?id=1",
              "source_notice_id": "2008-W070", "raw_extra": json.dumps({"page": 1}),
              "source_details": json.dumps({"version_order": {"basis": "agency_detail_id_order"}})}
    filing["dedupe_key"] = "f" * 40
    second = dict(filing, employees_affected=9, raw_extra=json.dumps({"page": 2}))
    for rec in (filing, second):
        rec["raw_record_hash"] = _record_hash(rec)
    ingest(conn, [filing, second], observed_at="2026-09-30")
    priors = [dict(row) for row in conn.execute("SELECT * FROM notices")]
    kept, _, report = project_ny(NY, existing_notices=priors)
    dashboard = next(r for r in kept if r["dedupe_key"] == example["dedupe_key"])
    [candidate] = json.loads(dashboard["source_details"])["possible_correspondence"]["candidates"]
    assert candidate["dedupe_key"] == filing["dedupe_key"]
    assert candidate["disposition"] == f"held_{HELD_RECOVERED_REASON}"
    held = report["held_recovered_filings"]
    assert list(held) == [filing["dedupe_key"]]
    assert {d["dedupe_key"] for d in held[filing["dedupe_key"]]["dashboard_notices"]} == {
        example["dedupe_key"]}
    withdrawn = withdraw_held_filings(conn, held)
    assert [rec["withdrawn_version"] for rec in withdrawn] == [1, 2]
    assert all(rec["possible_duplicate_of"][0]["dedupe_key"] == example["dedupe_key"]
               for rec in withdrawn)
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 0


def test_exact_version_target_and_unmatched_recovered_filings_are_kept():
    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"]
                   and "filing_group" not in json.loads(row["source_details"]))
    county = json.loads(example["source_details"])["raw_cells"][5]
    recovered = json.dumps({"entry": {"basis": "distinct_control_number"}})
    exact = {"id": 42, "dedupe_key": "k" * 40, "state": "NY",
             "employer_name": example["employer_name"], "location": county,
             "notice_date": example["notice_date"], "effective_date": None,
             "employees_affected": example["employees_affected"], "layoff_type": "closure",
             "is_temporary": None, "is_amendment": 0, "source_url": "u",
             "source_notice_id": "2008-W070", "source_details": recovered}
    unmatched = dict(exact, id=43, dedupe_key="u" * 40, employer_name="No Such Employer Ltd")
    _, _, report = project_ny(NY, existing_notices=[exact, unmatched])
    assert report["correspondence_versions"] == 1
    assert report["held_recovered_filings"] == {}
