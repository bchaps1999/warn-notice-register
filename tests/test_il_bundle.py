import gzip
import hashlib
import io
import json
import tarfile

import pytest
from openpyxl import Workbook
from warn import utils

from warnlive.migrate.il_bundle import build
from warnlive.migrate.offline_rebuild import rebuild
from warnlive.migrate.source_bundle import _entry, verify


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _base(path):
    files = {"raw/il.csv": b"old Illinois source\n", "raw/ma.csv": b"untouched\n"}
    manifest = {"format": "warn-source-bundle-v1", "admission_inputs": "agency-only-v1",
                "raw_overrides": [], "files": [
        {"path": name, "size": len(data), "sha256": _sha(data)}
        for name, data in sorted(files.items())
    ]}
    with path.open("wb") as raw, gzip.GzipFile(
        fileobj=raw, mode="wb", filename="", mtime=0
    ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
        header = (json.dumps(manifest) + "\n").encode()
        archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
        for name, data in sorted(files.items()):
            archive.addfile(_entry(name, data), io.BytesIO(data))


def _artifacts(path):
    path.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["IEBS Id", "Location Name"])
    sheet.append(["20260918003", "Example"])
    workbook.save(path / "export.xlsx")
    utils.write_rows_to_csv(path / "il.csv", utils.parse_excel(path / "export.xlsx"))
    (path / "guide.pdf").write_bytes(b"%PDF-1.4\nsynthetic test guide\n")
    files = []
    for name in ("export.xlsx", "il.csv", "guide.pdf"):
        data = (path / name).read_bytes()
        files.append({"path": name, "size": len(data), "sha256": _sha(data)})
    (path / "manifest.json").write_text(json.dumps({
        "capture": "il-iebs-live-2026-09-23", "files": files,
        "raw_rows": 1, "distinct_iebs_ids": 1,
    }))


def test_illinois_bundle_revision_is_deterministic_and_preserves_other_sources(tmp_path):
    base, artifacts = tmp_path / "base.tar.gz", tmp_path / "il"
    _base(base)
    _artifacts(artifacts)
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    build(base, artifacts, first)
    build(base, artifacts, second)
    assert first.read_bytes() == second.read_bytes()
    manifest = verify(first)
    assert "raw/il.csv" in manifest["raw_overrides"]
    with tarfile.open(first, "r:gz") as archive:
        assert archive.extractfile("raw/il.csv").read() == (artifacts / "il.csv").read_bytes()
        assert archive.extractfile("agency/il/il.csv").read() == (artifacts / "il.csv").read_bytes()
        assert archive.extractfile("raw/ma.csv").read() == b"untouched\n"


def test_illinois_bundle_rejects_csv_not_derived_from_workbook(tmp_path):
    base, artifacts = tmp_path / "base.tar.gz", tmp_path / "il"
    _base(base)
    _artifacts(artifacts)
    (artifacts / "il.csv").write_text("IEBS Id,Location Name\nother,Example\n")
    data = (artifacts / "il.csv").read_bytes()
    manifest_path = artifacts / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    next(item for item in manifest["files"] if item["path"] == "il.csv").update(
        size=len(data), sha256=_sha(data)
    )
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not reproduce"):
        build(base, artifacts, tmp_path / "invalid.tar.gz")
    assert not (tmp_path / "invalid.tar.gz").exists()


def test_replay_rejects_illinois_raw_csv_that_differs_from_pinned_export(tmp_path):
    base, artifacts = tmp_path / "base.tar.gz", tmp_path / "il"
    _base(base)
    _artifacts(artifacts)
    good, bad = tmp_path / "good.tar.gz", tmp_path / "bad.tar.gz"
    build(base, artifacts, good)
    with tarfile.open(good, "r:gz") as archive:
        files = {member.name: archive.extractfile(member).read()
                 for member in archive.getmembers() if member.name != "manifest.json"}
        manifest = json.load(archive.extractfile("manifest.json"))
    files["raw/il.csv"] = b"IEBS Id,Location Name\nwrong,Example\n"
    item = next(item for item in manifest["files"] if item["path"] == "raw/il.csv")
    item.update(size=len(files["raw/il.csv"]), sha256=_sha(files["raw/il.csv"]))
    with bad.open("wb") as raw, gzip.GzipFile(
        fileobj=raw, mode="wb", filename="", mtime=0
    ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
        header = (json.dumps(manifest) + "\n").encode()
        archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
        for name, data in sorted(files.items()):
            archive.addfile(_entry(name, data), io.BytesIO(data))
    with pytest.raises(ValueError, match="differs from current raw"):
        rebuild(bad, tmp_path / "candidate.sqlite", "2026-09-23", source_only=True)
    assert not (tmp_path / "candidate.sqlite").exists()
