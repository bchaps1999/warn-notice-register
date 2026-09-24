import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.mo_historical_source import project, read_artifacts


ARTIFACTS = (Path(__file__).resolve().parents[1]
             / "data/source_snapshots/mo/historical-1997-2019")


def test_missouri_workbook_accounts_for_every_company_row():
    rows, manifest = read_artifacts(ARTIFACTS)
    admitted, held, report = project(ARTIFACTS)
    assert manifest["sha256"] == "3e783842d6086e7f176c20d81de6906873c1bb2903126a3ceee900adece8141c"
    assert len(rows) == report["source_rows"] == 1301
    assert (len(admitted), len(held)) == (17, 1284)
    assert report["hold_reasons"] == {"unreviewed_rapid_response_row": 1284}
    assert len({rec["dedupe_key"] for rec in admitted}) == 17
    assert all(rec["notice_date"] is None and rec["source_notice_id"] is None
               and rec["source_identity"] is None for rec in admitted)
    assert all(rec["employees_affected"] > 0 for rec in admitted)
    penske = next(rec for rec in admitted if rec["employer_name"].startswith("Penske Logistics"))
    assert penske["effective_date"] is None
    assert "moved forward" in json.loads(penske["source_details"])["action_date_conflict"]
    assert all(json.loads(rec["source_details"])["agency_received_date"]
               for rec in admitted)
    assert {rec["employer_name"] for rec in admitted}.isdisjoint(
        {"Barnes Jewish Hospital", "Fresh Express (formerly Redi Cut Foods)",
         "Kirchhoff Van-Rob Shawnee"})
    assert any(item["source_sheet"] == "2018-19 Data" and item["physical_row"] == 4
               for item in held)
    assert all(item["source_row_sha256"] and item["raw_extra"] for item in held)


def test_missouri_current_event_overlap_is_held():
    admitted, held, report = project(ARTIFACTS, {("teepak", "2006-06-05")})
    assert len(admitted) == 16
    assert report["hold_reasons"]["current_agency_event_overlap"] == 1
    assert any(item["reason"] == "current_agency_event_overlap"
               and item["source_sheet"] == "2005-06 Data"
               and item["physical_row"] == 39 for item in held)


def test_missouri_workbook_rejects_changed_bytes(tmp_path):
    shutil.copytree(ARTIFACTS, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "WARN_Data1997-2018.xlsx"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
