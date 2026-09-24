import gzip
import hashlib
import io
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from warnlive.migrate.oh_annual_source import COUNTS, build, project, read_artifacts
from warnlive.migrate.source_bundle import _entry, verify


ARTIFACTS = (Path(__file__).resolve().parents[1]
             / "data/source_snapshots/oh/official-2015-2025")


def test_ohio_annual_rows_dates_and_ids_are_accounted_for():
    rows, _ = read_artifacts(ARTIFACTS)
    admitted, held, report = project(ARTIFACTS)
    assert len(rows) == sum(COUNTS.values()) == 898
    assert (len(admitted), len(held)) == (854, 44)
    assert report["admitted"] + report["held"] == 898
    assert len({r["source_identity"] for r in admitted}) == len(admitted)
    assert all(r["notice_date"] is None for r in admitted)
    assert all(json.loads(r["source_details"])["date_roles"]["Date Received"] == "agency_receipt"
               for r in admitted)
    assert all(r["source_row_sha256"] and r["raw_extra"] for r in held)
    assert any(r["year"] == 2015 and r["raw"]["notice_id"].endswith("14‐033")
               for r in rows)  # prior ID series within agency's 2015 table
    assert any(r["raw"]["continuation_rows"] for r in rows if r["year"] == 2018)
    assert any(r["effective_date"] is None and "to" in json.loads(r["raw_extra"])["layoff_dates"]
               for r in admitted)
    assert report["hold_reasons"]["conflicting_original_notice_id"] == 6
    assert report["hold_reasons"]["multiple_sites_in_source_row"] == 11
    assert report["revision_observations"] == 25
    assert all(r["related_source_row"] for r in held if r["disposition"] == "revision")
    assert all(r["disposition"] != "notice" for r in held)
    assert next(r for r in admitted if r["source_notice_id"] == "011-20-126")["employees_affected"] == 186


def test_ohio_amendment_does_not_create_second_notice():
    admitted, held, _ = project(ARTIFACTS)
    assert [r for r in admitted if r["source_notice_id"] == "018-18-022"]
    assert len([r for r in held if r["source_notice_id"] == "018-18-022"]) == 1
    assert not [r for r in admitted if r["source_notice_id"] == "003-21-018"]
    assert len([r for r in held if r["source_notice_id"] == "003-21-018"]) == 2
    admitted2, held2, report2 = project(ARTIFACTS, {"OH:018-18-022"})
    assert len(admitted2) == len(admitted) - 1
    assert len(held2) == len(held) + 1
    assert report2["hold_reasons"]["already_represented_oh_id"] == 2


def test_multisite_continuations_are_held_and_revision_markers_are_preserved():
    admitted, held, _ = project(ARTIFACTS)
    for ident in ("020-16-036", "009-16-066"):
        assert not [r for r in admitted if r["source_notice_id"] == ident]
        matches = [r for r in held if r["source_notice_id"] == ident]
        assert len(matches) == 1
        assert matches[0]["reason"] == "multiple_sites_in_source_row"
        assert json.loads(matches[0]["raw_extra"])["continuation_rows"]
    ptc = [r for r in admitted if r["source_notice_id"] == "006-18-009"]
    assert len(ptc) == 1 and ptc[0]["is_amendment"] == 1
    assert "Revised" in json.loads(ptc[0]["raw_extra"])["continuation_rows"][0]["cells"][0]


def test_ohio_source_checksum_is_pinned(tmp_path):
    shutil.copytree(ARTIFACTS, tmp_path, dirs_exist_ok=True)
    path = tmp_path / "WARN2022.html"
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)


def test_ohio_bundle_overlay_preserves_base_and_checks_artifacts(tmp_path):
    base_path, out_path = tmp_path / "base.tar.gz", tmp_path / "oh.tar.gz"
    payload = b"pinned base input\n"
    name = "raw/other.csv"
    manifest = {"format": "warn-source-bundle-v1", "admission_inputs": "agency-only-v1",
                "files": [{"path": name, "size": len(payload),
                           "sha256": hashlib.sha256(payload).hexdigest()}]}
    header = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    with base_path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
        archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
        archive.addfile(_entry(name, payload), io.BytesIO(payload))
    result = build(base_path, ARTIFACTS, out_path)
    assert result == verify(out_path)
    assert len(result["files"]) == 10  # base, source manifest, eight annual artifacts
    with tarfile.open(out_path, "r:gz") as archive:
        assert archive.extractfile(name).read() == payload
        assert archive.extractfile("agency/oh_annual/WARN2015.pdf").read() == (ARTIFACTS / "WARN2015.pdf").read_bytes()
    with pytest.raises(FileExistsError):
        build(base_path, ARTIFACTS, out_path)
