import json
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
    assert (len(admitted), len(held)) == (783, 148)
    assert (report["itemized_filings"], report["admitted_rows"]) == (59, 934)
    assert report["hold_reasons"] == {
        "newer_agency_capture_overlap": 80,
        "multi_site_or_phase_identity_unresolved": 2,
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


def test_partial_rows_rule_admits_rows_missing_only_date_or_count():
    admitted, held, report = project(HISTORICAL, _current_ids(), admit_partial_rows=True)
    assert (len(admitted), len(held), report["partial_rows_admitted"]) == (828, 103, 45)
    assert report["hold_reasons"] == {
        "newer_agency_capture_overlap": 80,
        "multi_site_or_phase_identity_unresolved": 2,
        "duplicate_agency_capture": 1,
        "unresolved_employer_in_source": 16,
        "partial_row_same_employer_and_received_date": 2,
        "placeholder_warn_number": 1,
        "missing_warn_number": 1,
    }
    by_id = {row["source_notice_id"]: row for row in admitted}
    # 1667 lists neither a layoff date nor a count; the filing is kept.
    burley = by_id["1667"]
    assert (burley["effective_date"], burley["effective_date_precision"],
            burley["employees_affected"]) == (None, None, None)
    assert json.loads(burley["source_details"])["partial_row"] == {
        "rule": "or_historical_partial_row_v1", "blank_fields": ["Layoff Date", "Laid Off"]}
    # 0593 has a count and Excel's zero-date sentinel for its layoff date.
    assert (by_id["0593"]["employees_affected"], by_id["0593"]["effective_date"]) == (1600, None)
    # A place-only Company Name, the placeholder number and the Amalgamated
    # Sugar pair (two numbers, one received day) stay held.
    reasons = {row["source_notice_id"]: row["reason"] for row in held}
    assert reasons["0838"] == "unresolved_employer_in_source"
    assert reasons["0000"] == "placeholder_warn_number"
    assert reasons["0942"] == reasons["1004"] == "partial_row_same_employer_and_received_date"
    # Rows admitted under both rules are identical.
    before = {row["dedupe_key"]: row for row in project(HISTORICAL, _current_ids())[0]}
    assert all(by_key == before[by_key["dedupe_key"]] for by_key in admitted
               if by_key["dedupe_key"] in before)
