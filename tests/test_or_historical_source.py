import shutil
from pathlib import Path

import pytest

from warnlive.migrate.or_historical_source import project, read_artifacts
from warnlive.migrate.or_source import read_artifacts as current_rows


ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/or"
HISTORICAL = ROOT / "historical-1980-2021"


def _current_ids():
    rows, _ = current_rows(ROOT)
    return {str(row["raw"]["WARN#"] or "") for row in rows}


def test_historical_oregon_accounts_for_every_pinned_row():
    rows, _ = read_artifacts(HISTORICAL)
    admitted, held, report = project(HISTORICAL, _current_ids())
    assert len(rows) == 1082
    assert (len(admitted), len(held)) == (724, 358)
    assert report["hold_reasons"] == {
        "newer_agency_capture_overlap": 80,
        "multi_site_or_phase_identity_unresolved": 212,
        "duplicate_agency_capture": 1,
        "incomplete_historical_row": 58,
        "unresolved_employer_in_source": 6,
        "missing_warn_number": 1,
    }
    assert len({row["source_identity"] for row in admitted}) == len(admitted)
    assert all(row["notice_date"] is None for row in admitted)
    assert all(row["source_identity"].startswith("OR:agency:") for row in admitted)
    assert all(row["effective_date"] >= "1980-01-01" for row in admitted)
    assert not {"0826", "0810", "0809", "0742", "0727", "0708"} & {
        row["source_notice_id"] for row in admitted
    }
    assert {row["reason"] for row in held if row["source_notice_id"] == "0942"} == {
        "incomplete_historical_row"
    }


def test_historical_oregon_rejects_changed_workbook_bytes(tmp_path):
    shutil.copytree(HISTORICAL, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "or_warnlist_july_2021.xlsx"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
