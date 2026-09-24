import json
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.tx_historical_source import project, read_artifacts


HISTORICAL = (Path(__file__).resolve().parents[1]
              / "data/source_snapshots/tx/historical-1989-2019")


def test_texas_workbook_accounts_for_every_numbered_notice_and_keeps_date_roles():
    rows, manifest = read_artifacts(HISTORICAL)
    admitted, held, report = project(HISTORICAL)
    assert len(rows) == report["source_rows"] == 5097
    assert len(admitted) == 5086
    assert len(held) == 11
    assert report["hold_reasons"] == {"missing_location": 10, "anomalous_action_year": 1}
    assert report["admitted_missing_workers"] == 24
    assert report["admitted_missing_action_date"] == 8
    assert len({row["source_identity"] for row in admitted}) == len(admitted)
    assert all(row["source_identity"] == "TX:" + row["source_notice_id"] for row in admitted)
    assert all(row["source_url"] == manifest["source_url"] for row in admitted)
    first = next(row for row in admitted if row["source_notice_id"] == "2299")
    assert first["notice_date"] == "1999-01-04"
    assert first["effective_date"] == "1999-03-05"
    details = json.loads(first["source_details"])
    assert details["agency_received_date"] == "1999-01-04"
    assert details["date_roles"] == {
        "NOTICE_DATE": "reported_notice", "LayOff_Date": "reported_action",
        "WFDD_RECEIVED_DATE": "agency_receipt",
    }
    assert any(row["source_notice_id"] == "17675"
               and row["reason"] == "anomalous_action_year" for row in held)


def test_texas_workbook_holds_existing_identity_and_event():
    admitted, held, report = project(
        HISTORICAL, {"2299"},
        {("american medical response", "1999-01-07", "port arthur", "jefferson")})
    assert report["admitted"] == 5084
    assert report["hold_reasons"]["already_represented_tx_id"] == 1
    assert report["hold_reasons"]["idless_current_event_overlap"] == 1
    assert {row["source_notice_id"] for row in held} >= {"2299", "359"}
    assert len(admitted) + len(held) == 5097


def test_texas_workbook_rejects_changed_bytes_and_manifest(tmp_path):
    shutil.copytree(HISTORICAL, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "tx_historical.xlsx"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
    path.write_bytes((HISTORICAL / "tx_historical.xlsx").read_bytes())
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="unsupported Texas historical workbook manifest"):
        read_artifacts(tmp_path)
