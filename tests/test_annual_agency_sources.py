"""Pinned annual agency inputs keep source rows and admission decisions auditable."""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.ga_archive_source import project as project_ga
from warnlive.migrate.ny_annual_source import project as project_ny, read_artifacts


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


def test_ny_annual_source_holds_existing_employer_and_site():
    records, _, _ = project_ny(NY)
    example = records[0]
    reduced, held, _ = project_ny(
        NY, {example["employer_name"]},
        {(example["location"].casefold(), example["effective_date"], example["employees_affected"])},
    )
    assert len(reduced) == len(records) - 1
    assert any(row["reason"] == "existing_employer_event_identity_unresolved" for row in held)
    reduced, held, _ = project_ny(
        NY, existing_site_actions={
            (example["location"].casefold(), example["effective_date"], example["employees_affected"])
        },
    )
    assert len(reduced) == len(records) - 1
    assert any(row["reason"] == "existing_site_action_worker_identity_unresolved" for row in held)


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
