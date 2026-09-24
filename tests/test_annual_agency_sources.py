"""Pinned annual agency inputs keep source rows and admission decisions auditable."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.ga_archive_source import project as project_ga
from warnlive.migrate.ny_annual_source import project as project_ny, read_artifacts
from warnlive.migrate.ny_overlay import _employer_group


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
    assert report["held_multi_site_rows"] > 2000
    assert all(row["disposition"] == "unresolved" for row in held)
    assert all(row["filing_group_sites"] > 1 and row["filing_group_rows"] >= row["filing_group_sites"]
               for row in held if row["reason"] == "multi_site_filing_identity_unresolved")
    assert all(json.loads(row["source_details"])["disposition"] == "notice" for row in records)


def test_ny_same_filing_signature_across_sites_is_held_as_a_group():
    _, held, _ = project_ny(NY)
    fanout = [row for row in held if row["reason"] == "multi_site_filing_identity_unresolved"]
    assert fanout
    assert max(row["filing_group_rows"] for row in fanout) > 100
    assert all(row["disposition_evidence"] == "same_employer_notice_multiple_sites_or_phases"
               for row in fanout)
    phased = [row for row in fanout if json.loads(row["raw_extra"])["company"] ==
              "American Transit Inc." and row["notice_year"] == "2008"]
    assert len(phased) >= 2
    assert len({json.loads(row["raw_extra"])["effective_date"] for row in phased}) > 1


def test_ny_historical_id_correspondence_survives_different_site_granularity():
    records, _, _ = project_ny(NY)
    example = next(row for row in records if row["employees_affected"] and row["effective_date"])
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
    assert any(row["disposition_evidence"] == "same_employer_notice_date_workers_county"
               for row in correspondence)


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
