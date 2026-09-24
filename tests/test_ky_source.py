"""Pinned Kentucky agency report projection and source bundle evidence."""

import gzip
import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from warnlive.migrate.ky_source import FILENAME, SHA256, build, project, read_artifacts
from warnlive.migrate.source_bundle import _entry, verify


ARTIFACTS = (Path(__file__).resolve().parents[1]
             / "data/source_snapshots/ky/official-2026")


def test_kentucky_rows_are_accounted_for_with_distinct_date_roles():
    rows = read_artifacts(ARTIFACTS)
    admitted, held, report = project(ARTIFACTS)
    assert (len(rows), len(admitted), len(held)) == (35, 33, 2)
    assert report == {
        "source_rows": 35, "admitted": 33, "held": 2,
        "hold_reasons": {"source_explicitly_out_of_state": 2},
        "admitted_workers": 4316,
    }
    assert len({row["source_row"] for row in rows}) == 35
    assert len({row["source_identity"] for row in admitted}) == 33
    assert {row["source_notice_id"] for row in held} == {"2833", "2778"}
    assert all(row["notice_date"] is None for row in admitted)
    assert all(row["effective_date_precision"] == "day" for row in admitted)
    assert all(row["source_artifact_sha256"] == SHA256 for row in rows)
    assert all(row["source_row_sha256"] in row["source_row"] for row in rows)
    assert all(row["source_row_sha256"] and row["raw_extra"] for row in held)
    parsons = next(row for row in admitted if row["source_identity"] == "KY:2839")
    details = json.loads(parsons["source_details"])
    assert parsons["effective_date"] == "2026-11-19"
    assert details["agency_received_date"] == "2026-09-10"
    assert details["date_roles"] == {
        "Date Received": "agency_receipt", "Projected Date": "projected_action",
    }
    assert details["source_row_sha256"] in details["source_row"]
    assert details["source_document_url"] == json.loads(parsons["raw_extra"])["Notice URL"]
    assert {row["source_notice_id"] for row in admitted}.isdisjoint(
        {row["source_notice_id"] for row in held})


def test_kentucky_manifest_and_csv_checksums_fail_closed(tmp_path):
    shutil.copytree(ARTIFACTS, tmp_path, dirs_exist_ok=True)
    csv_path = tmp_path / FILENAME
    csv_path.write_bytes(csv_path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
    shutil.copy2(ARTIFACTS / FILENAME, csv_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["in_state_rows"] = 34
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="manifest changed"):
        read_artifacts(tmp_path)


def _base_bundle(path: Path, *, state_raw: bool = False) -> bytes:
    payload = b"existing agency-only input\n"
    name = "raw/ky.csv" if state_raw else "raw/other.csv"
    manifest = {
        "format": "warn-source-bundle-v1", "admission_inputs": "agency-only-v1",
        "files": [{"path": name, "size": len(payload),
                   "sha256": hashlib.sha256(payload).hexdigest()}],
    }
    header = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    with path.open("wb") as raw, gzip.GzipFile(
        fileobj=raw, mode="wb", filename="", mtime=0
    ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
        archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
        archive.addfile(_entry(name, payload), io.BytesIO(payload))
    return payload


def test_kentucky_overlay_preserves_base_and_is_deterministic(tmp_path):
    base = tmp_path / "base.tar.gz"
    first, second = tmp_path / "ky-a.tar.gz", tmp_path / "ky-b.tar.gz"
    payload = _base_bundle(base)
    manifest = build(base, ARTIFACTS, first)
    assert manifest == verify(first)
    assert len(manifest["files"]) == 3
    with tarfile.open(first, "r:gz") as archive:
        assert archive.extractfile("raw/other.csv").read() == payload
        assert archive.extractfile(f"agency/ky/{FILENAME}").read() == (ARTIFACTS / FILENAME).read_bytes()
        assert archive.extractfile("agency/ky/manifest.json").read() == (ARTIFACTS / "manifest.json").read_bytes()
    build(base, ARTIFACTS, second)
    assert first.read_bytes() == second.read_bytes()
    with pytest.raises(FileExistsError):
        build(base, ARTIFACTS, first)


def test_kentucky_overlay_rejects_old_unverified_raw_capture(tmp_path):
    base = tmp_path / "unverified.tar.gz"
    _base_bundle(base, state_raw=True)
    with pytest.raises(ValueError, match="unverified Kentucky raw capture"):
        build(base, ARTIFACTS, tmp_path / "candidate.tar.gz")
