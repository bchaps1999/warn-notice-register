import json
import csv
import hashlib
import shutil
from pathlib import Path

import pytest

from warnlive.migrate.ny_source import HEADERS, read_artifacts


SOURCE = Path(__file__).resolve().parents[1] / "data/source_snapshots/ny"


def test_pinned_new_york_rows_preserve_duplicate_columns_and_dates():
    rows = read_artifacts(SOURCE)
    assert len(rows) == 132 + 285 + 341
    assert len({row["source_row_id"] for row in rows}) == len(rows)
    assert rows[0]["headers"] == list(HEADERS)
    assert rows[0]["headers"][10] == rows[0]["headers"][11]
    assert rows[0]["raw_cells"][10:] == ["107", "107"]
    assert rows[0]["notice_date"] == "2022-10-13"
    assert rows[0]["effective_date"] == "2023-01-13"
    example = next(row for row in rows if row["company"] == "3E Logistics NJ, Inc.")
    assert example["notice_date"] == "2024-09-16"
    assert example["posted_date"] == example["effective_date"] == "2024-09-30"
    assert example["workers"] == "84"
    first_transit = next(row for row in rows if row["source_row_id"].endswith(
        ":row:101:sha256:45e7272f3704e04229fd99e876e13c0154badc6fc8ebbbd46d740b87f65aa751"))
    assert sum(row["company"] == first_transit["company"] for row in rows) == 1
    assert first_transit["company"] == (
        "First Transit, Inc. a subsidiary of Transdev, North America Inc.")
    assert first_transit["address"] == "2700 Millersport Hwy  Getzville, NY, 14068"
    assert first_transit["county"] == "Erie"
    assert first_transit["workers"] == "65"
    assert (first_transit["notice_date"], first_transit["posted_date"],
            first_transit["effective_date"]) == ("2024-03-21", "2024-03-22", "2024-06-30")


def test_new_york_reader_rejects_changed_bytes(tmp_path):
    shutil.copytree(SOURCE, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "ny_warn_2022.csv"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)


def test_new_york_reader_rejects_changed_manifest_schema(tmp_path):
    shutil.copytree(SOURCE, tmp_path, dirs_exist_ok=True)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["artifacts"][0]["headers"][11] = "renamed"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="schema mismatch"):
        read_artifacts(tmp_path)


def test_new_york_reader_rejects_disagreeing_duplicate_worker_columns(tmp_path):
    shutil.copytree(SOURCE, tmp_path, dirs_exist_ok=True)
    csv_path = tmp_path / "ny_warn_2022.csv"
    with csv_path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.reader(stream))
    rows[1][11] = "108"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    payload = csv_path.read_bytes()
    manifest["artifacts"][0]["bytes"] = len(payload)
    manifest["artifacts"][0]["sha256"] = hashlib.sha256(payload).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="worker columns disagree"):
        read_artifacts(tmp_path)


def test_new_york_reader_rejects_changed_filing_and_decision(tmp_path):
    shutil.copytree(SOURCE, tmp_path, dirs_exist_ok=True)
    filing = tmp_path / "filings/3e-logistics-2024-0066.pdf"
    filing.write_bytes(filing.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
    shutil.copy2(SOURCE / "filings/3e-logistics-2024-0066.pdf", filing)
    decision = tmp_path / "reviewed_date_repairs.json"
    decision.write_bytes(decision.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)


def test_new_york_reader_rejects_changed_first_transit_filing(tmp_path):
    shutil.copytree(SOURCE, tmp_path, dirs_exist_ok=True)
    filing = tmp_path / "filings/first-transit-2023-0298.pdf"
    filing.write_bytes(filing.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)


def test_new_york_reader_accepts_symlinked_parent_of_artifact_root(tmp_path):
    alias = tmp_path / "artifact-alias"
    alias.symlink_to(SOURCE, target_is_directory=True)
    assert len(read_artifacts(alias)) == 758
