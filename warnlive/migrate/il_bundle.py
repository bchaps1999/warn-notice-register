"""Derive a frozen source bundle with a verified fresh Illinois IEBS export."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory

from warn import utils

from warnlive.migrate.source_bundle import _entry, verify


def _check_artifacts(directory: Path) -> dict[str, bytes]:
    manifest_path = directory / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("capture") != "il-iebs-live-2026-09-23":
        raise ValueError("unexpected Illinois capture")
    listed = {item["path"]: item for item in manifest["files"]}
    if set(listed) != {"export.xlsx", "il.csv", "guide.pdf"}:
        raise ValueError("Illinois manifest must list workbook, derived CSV, and guide")
    data = {"manifest.json": manifest_bytes}
    for name, item in listed.items():
        content = (directory / name).read_bytes()
        if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValueError(f"Illinois artifact checksum mismatch: {name}")
        data[name] = content
    with TemporaryDirectory(prefix="warn-il-verify-") as temp:
        workbook = Path(temp) / "export.xlsx"
        generated = Path(temp) / "il.csv"
        workbook.write_bytes(data["export.xlsx"])
        utils.write_rows_to_csv(generated, utils.parse_excel(workbook))
        if generated.read_bytes() != data["il.csv"]:
            raise ValueError("Illinois CSV does not reproduce from pinned workbook")
    rows = list(csv.DictReader(io.StringIO(data["il.csv"].decode("utf-8-sig"))))
    ids = [(row.get("IEBS Id") or "").strip() for row in rows]
    if len(rows) != manifest["raw_rows"] or len(ids) != len(set(ids)) or not all(ids):
        raise ValueError("Illinois IEBS row inventory mismatch")
    if len(ids) != manifest["distinct_iebs_ids"]:
        raise ValueError("Illinois IEBS ID count mismatch")
    return data


def build(base_bundle: Path, artifacts: Path, out_bundle: Path) -> dict:
    """Replace only current raw/il.csv; preserve every earlier source byte."""
    base_bundle, artifacts, out_bundle = map(Path, (base_bundle, artifacts, out_bundle))
    if out_bundle.exists():
        raise FileExistsError(out_bundle)
    base_manifest = verify(base_bundle)
    il = _check_artifacts(artifacts)
    with tarfile.open(base_bundle, "r:gz") as archive:
        files = {
            member.name: archive.extractfile(member).read()
            for member in archive.getmembers() if member.name != "manifest.json"
        }
    if "raw/il.csv" not in files or any(name.startswith("agency/il/") for name in files):
        raise ValueError("base bundle is missing raw Illinois or already has Illinois agency artifacts")
    files["raw/il.csv"] = il["il.csv"]
    files["agency/il/manifest.json"] = il["manifest.json"]
    files["agency/il/export.xlsx"] = il["export.xlsx"]
    files["agency/il/il.csv"] = il["il.csv"]
    files["agency/il/guide.pdf"] = il["guide.pdf"]
    manifest = {
        **base_manifest,
        "files": [{
            "path": name, "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        } for name, content in sorted(files.items())],
        "raw_overrides": sorted(set(base_manifest.get("raw_overrides", [])) | {"raw/il.csv"}),
    }
    out_bundle.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_bundle.open("xb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", filename="", mtime=0
        ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            header = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            archive.addfile(_entry("manifest.json", header), io.BytesIO(header))
            for name, content in sorted(files.items()):
                archive.addfile(_entry(name, content), io.BytesIO(content))
        verify(out_bundle)
    except BaseException:
        out_bundle.unlink(missing_ok=True)
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.base, args.artifacts, args.out)
    print(json.dumps({"files": len(result["files"]), "out": str(args.out)}, sort_keys=True))


if __name__ == "__main__":
    main()
