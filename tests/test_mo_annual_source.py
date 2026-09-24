import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.mo_annual_source import project, read_artifacts


ARTIFACTS = (Path(__file__).resolve().parents[1]
             / "data/source_snapshots/mo/annual-2019-2024")


def test_missouri_annual_tables_account_for_every_row():
    rows, _ = read_artifacts(ARTIFACTS)
    admitted, held, report = project(ARTIFACTS)
    assert len(rows) == 297
    assert (len(admitted), len(held)) == (295, 2)
    assert report["hold_reasons"] == {"zero_affected_no_event": 1,
                                       "out_of_state_location": 1}
    assert report["admitted"] + report["held"] == 297
    assert len({item["dedupe_key"] for item in admitted}) == len(admitted)
    assert all(item["notice_date"] is None and item["source_notice_id"] is None
               for item in admitted)
    assert all(json.loads(item["source_details"])["agency_received_dates"]
               for item in admitted)
    assert all(item["source_row_sha256"] and item["raw_extra"] for item in held)
    assert any(item["employer_name"] == "GKN Aerospace"
               and item["effective_date"] is None for item in admitted)
    assert any(item["employer_name"] == "Multi-Color Corporation"
               and item["effective_date"] is None for item in admitted)
    assert any(item["employer_name"] == "Sodecia Automotive Kansas City, LLC"
               and item["is_temporary"] is True for item in admitted)


def test_missouri_annual_current_event_overlap_is_held():
    admitted, held, report = project(ARTIFACTS, {("beauty brands", "2019-01-18")})
    assert report["hold_reasons"].get("current_agency_event_overlap") == 1
    assert any(item["reason"] == "current_agency_event_overlap" for item in held)


def test_missouri_annual_rejects_changed_bytes(tmp_path):
    shutil.copytree(ARTIFACTS, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "2024.webtext.txt"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
