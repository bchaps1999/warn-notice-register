"""Freeze and verify the local source inputs needed for an offline rebuild.

The bundle is an input archive, not a database backup.  It records the exact
bytes of rolling raw CSVs, frozen agency archives, and cached agency artifacts
so a later rebuild is not dependent on whatever a state serves that day.
Every bundle is agency-only (``admission_inputs: agency-only-v1``): no Big
Local News table or old-database policy enters it.

Commands: ``create`` (from a workdir), ``verify``, ``overlay-raw`` (replace
one state CSV in a frozen bundle), ``add-archives`` (add new
``backfill/cache/archives`` files to a frozen bundle), ``add-agency`` (add a
new pinned ``agency/<name>/`` artifact directory) and ``extract``. Every
derived bundle is a new file; none is modified in place. No network access.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import tarfile
from pathlib import Path, PurePosixPath

REQUIRED = ("raw", "backfill/cache/archives", "cache/sc")
OPTIONAL = (
    "cache/il_reports",
)

PINNED_AGENCY_RAW_SHA256 = {
    "ga": "7f52711bbbe11339e8df33536b3f6c6b34e1ac1ea7225bfe7dfa3d14e0e76d7f",
    "tn": "ed931aebac61a8146b2cb65646356aa88c61ae6658ec5280ac7b59a2ed5ee159",
}


def validate_agency_raw_file(postal: str, path: Path) -> None:
    """Reject known third-party history in one state capture."""
    if postal == "tx":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            for row in csv.DictReader(stream):
                day = row.get("NOTICE_DATE") or ""
                if len(day) < 4 or not day[:4].isdigit() or int(day[:4]) < 2020:
                    raise ValueError("Texas raw capture contains unverified historical rows")
    elif postal == "oh":
        with path.open(newline="", encoding="utf-8-sig") as stream:
            if any(not row.get("URL") for row in csv.DictReader(stream)):
                raise ValueError("Ohio raw capture contains third-party historical rows")
    elif postal in {"ia", "ky", "or"}:
        raise ValueError(f"{postal.upper()} raw capture has an unresolved BLN history boundary")
    elif postal in PINNED_AGENCY_RAW_SHA256:
        if hashlib.sha256(path.read_bytes()).hexdigest() != PINNED_AGENCY_RAW_SHA256[postal]:
            raise ValueError(f"{postal.upper()} raw capture is not the pinned agency-only projection")


def validate_agency_raw(raw_dir: Path) -> None:
    """Validate every present state capture with a known BLN mixed-source risk."""
    for postal in ("tx", "oh", "ia", "ky", "or", "ga", "tn"):
        path = raw_dir / f"{postal}.csv"
        if path.is_file():
            validate_agency_raw_file(postal, path)


def _files(root: Path) -> list[Path]:
    selected: set[Path] = set()
    for name in REQUIRED + OPTIONAL:
        path = root / name
        if not path.exists():
            if name in REQUIRED:
                raise FileNotFoundError(f"required source input missing: {path}")
            continue
        if path.is_symlink():
            raise ValueError(f"source symlink is not allowed: {path}")
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_symlink():
                raise ValueError(f"source symlink is not allowed: {candidate}")
            if candidate.is_file():
                selected.add(candidate.relative_to(root))
    return sorted(selected, key=lambda p: p.as_posix())


def _entry(name: str, content: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mtime = info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mode = 0o644
    return info


def create(
    workdir: Path, out_path: Path,
    raw_overlay: Path | None = None,
    agency_artifacts: Path | None = None,
    ia_artifacts: Path | None = None,
    ny_artifacts: Path | None = None,
    tx_artifacts: Path | None = None,
) -> dict:
    """Create a deterministic bundle; never overwrite an existing snapshot."""
    workdir, out_path = Path(workdir), Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"source bundle already exists: {out_path}")
    files = _files(workdir)
    overrides: dict[Path, Path] = {}
    if raw_overlay is not None:
        raw_overlay = Path(raw_overlay)
        if not raw_overlay.is_dir() or raw_overlay.is_symlink():
            raise ValueError(f"raw overlay must be a directory: {raw_overlay}")
        for candidate in raw_overlay.iterdir():
            if candidate.is_symlink() or not candidate.is_file():
                raise ValueError(f"raw overlay contains a non-file: {candidate}")
            if not re.fullmatch(r"[a-z]{2}\.csv", candidate.name):
                raise ValueError(f"raw overlay has an unexpected file: {candidate}")
            rel = Path("raw") / candidate.name
            if rel not in files:
                raise ValueError(f"raw overlay has no base snapshot: {candidate}")
            overrides[rel] = candidate
        if not overrides:
            raise ValueError("raw overlay contains no state CSVs")

    additional: dict[Path, Path] = {}
    if agency_artifacts is not None:
        agency_artifacts = Path(agency_artifacts)
        if agency_artifacts.is_symlink() or not agency_artifacts.is_dir():
            raise ValueError(f"invalid agency artifact directory: {agency_artifacts}")
        for name in ("manifest.json", "2025.pdf", "2026.pdf"):
            path = agency_artifacts / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"invalid agency artifact: {path}")
            additional[Path("agency/la") / name] = path
        from warnlive.migrate.la_source import extract as extract_la

        extract_la(agency_artifacts)  # verify bytes and layout before freezing
    if ia_artifacts is not None:
        ia_artifacts = Path(ia_artifacts)
        if ia_artifacts.is_symlink() or not ia_artifacts.is_dir():
            raise ValueError(f"invalid Iowa artifact directory: {ia_artifacts}")
        for name in ("manifest.json", "event-log.xlsx", "historical-2023.pdf"):
            path = ia_artifacts / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"invalid Iowa artifact: {path}")
            additional[Path("agency/ia") / name] = path
        from warnlive.migrate.ia_source import extract_historical as extract_ia

        extract_ia(ia_artifacts)  # verify both artifacts and row layout before freezing
    if ny_artifacts is not None:
        ny_artifacts = Path(ny_artifacts)
        if ny_artifacts.is_symlink() or not ny_artifacts.is_dir():
            raise ValueError(f"invalid New York artifact directory: {ny_artifacts}")
        for name in ("manifest.json", "ny_warn_2022.csv", "ny_warn_2023.csv",
                     "ny_warn_2024.csv"):
            path = ny_artifacts / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"invalid New York artifact: {path}")
            additional[Path("agency/ny") / name] = path
        from warnlive.migrate.ny_source import read_artifacts as read_ny, verified_extra_paths

        read_ny(ny_artifacts)  # verify bytes, year, and row layout before freezing
        ny_manifest = json.loads((ny_artifacts / "manifest.json").read_text())
        for name in verified_extra_paths(ny_artifacts, ny_manifest):
            additional[Path("agency/ny") / name] = ny_artifacts / name
    if tx_artifacts is not None:
        tx_artifacts = Path(tx_artifacts)
        if tx_artifacts.is_symlink() or not tx_artifacts.is_dir():
            raise ValueError(f"invalid Texas artifact directory: {tx_artifacts}")
        from warnlive.migrate.tx_source import read_artifacts as read_tx

        read_tx(tx_artifacts, overrides.get(Path("raw/tx.csv"), workdir / "raw/tx.csv"))
        for name in ("manifest.json", "source.html", *(f"{year}.xlsx" for year in range(2020, 2027))):
            path = tx_artifacts / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"invalid Texas artifact: {path}")
            additional[Path("agency/tx") / name] = path
    if set(additional) & set(files):
        raise ValueError("agency artifact collides with workdir input")
    files = sorted(set(files) | set(additional), key=lambda p: p.as_posix())

    def source_bytes(rel: Path) -> bytes:
        return overrides.get(rel, additional.get(rel, workdir / rel)).read_bytes()

    for name in ("tx", "oh", "ga", "tn", "ia", "ky", "or"):
        rel = Path("raw") / f"{name}.csv"
        if rel in files:
            validate_agency_raw_file(name, overrides.get(rel, workdir / rel))

    manifest = {"format": "warn-source-bundle-v1", "admission_inputs": "agency-only-v1", "files": [],
                "raw_overrides": sorted(rel.as_posix() for rel in overrides)}
    for rel in files:
        data = source_bytes(rel)
        manifest["files"].append({
            "path": rel.as_posix(), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with out_path.open("xb") as raw, gzip.GzipFile(
            fileobj=raw, mode="wb", filename="", mtime=0
        ) as zipped, tarfile.open(fileobj=zipped, mode="w") as archive:
            meta = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            archive.addfile(_entry("manifest.json", meta), io.BytesIO(meta))
            for rel in files:
                data = source_bytes(rel)
                archive.addfile(_entry(rel.as_posix(), data), io.BytesIO(data))
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    return manifest


def verify(bundle: Path) -> dict:
    """Verify path safety, completeness, sizes, and every payload checksum."""
    with tarfile.open(bundle, mode="r:gz") as archive:
        members = archive.getmembers()
        if not members or members[0].name != "manifest.json":
            raise ValueError("source bundle has no leading manifest")
        if any(not item.isfile() for item in members):
            raise ValueError("source bundle contains a non-file member")
        manifest_file = archive.extractfile(members[0])
        if manifest_file is None:
            raise ValueError("source manifest is unreadable")
        manifest = json.load(manifest_file)
        if manifest.get("format") != "warn-source-bundle-v1":
            raise ValueError("unsupported source bundle format")
        listed = manifest.get("files")
        if not isinstance(listed, list):
            raise ValueError("source manifest has no file list")
        expected = {item["path"]: item for item in listed}
        if len(expected) != len(listed) or len(members) != len(listed) + 1:
            raise ValueError("duplicate or missing source bundle members")
        for member in members[1:]:
            path = PurePosixPath(member.name)
            if path.is_absolute() or ".." in path.parts or member.name not in expected:
                raise ValueError(f"unexpected source path: {member.name}")
            item = expected[member.name]
            if member.size != item["size"]:
                raise ValueError(f"source size mismatch: {member.name}")
            content = archive.extractfile(member)
            if content is None:
                raise ValueError(f"unreadable source: {member.name}")
            digest = hashlib.sha256(content.read()).hexdigest()
            if digest != item["sha256"]:
                raise ValueError(f"source checksum mismatch: {member.name}")
        if set(expected) != {member.name for member in members[1:]}:
            raise ValueError("source manifest does not match bundle members")
    return manifest


def overlay_raw_bundle(bundle: Path, raw_file: Path, out_path: Path,
                       evidence_archive: Path | None = None) -> dict:
    """Replace one state CSV in a verified frozen bundle without changing other inputs."""
    bundle, raw_file, out_path = Path(bundle), Path(raw_file), Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"source bundle already exists: {out_path}")
    if raw_file.is_symlink() or not raw_file.is_file() or not re.fullmatch(
        r"[a-z]{2}\.csv", raw_file.name
    ):
        raise ValueError(f"invalid raw overlay: {raw_file}")
    postal = raw_file.stem
    validate_agency_raw_file(postal, raw_file)
    replacement = raw_file.read_bytes()
    manifest = verify(bundle)
    member_name = f"raw/{raw_file.name}"
    if member_name not in {item["path"] for item in manifest["files"]}:
        raise ValueError(f"raw overlay has no base member: {member_name}")
    manifest = json.loads(json.dumps(manifest))
    for item in manifest["files"]:
        if item["path"] == member_name:
            item["size"] = len(replacement)
            item["sha256"] = hashlib.sha256(replacement).hexdigest()
    manifest["raw_overrides"] = sorted(set(manifest.get("raw_overrides", [])) | {member_name})
    if evidence_archive is not None:
        evidence_archive = Path(evidence_archive)
        if not evidence_archive.is_file() or evidence_archive.is_symlink():
            raise ValueError(f"invalid companion evidence archive: {evidence_archive}")
        manifest["companion_evidence"] = {
            "name": evidence_archive.name,
            "sha256": hashlib.sha256(evidence_archive.read_bytes()).hexdigest(),
            "raw_member": member_name,
            "raw_sha256": hashlib.sha256(replacement).hexdigest(),
        }
    return _rewrite(bundle, manifest, {member_name: replacement}, out_path)


def _rewrite(bundle: Path, manifest: dict, new_content: dict[str, bytes], out_path: Path) -> dict:
    """Write ``manifest`` and its files deterministically (mtime 0, manifest
    order): members named in ``new_content`` take those bytes, every other
    member is copied from ``bundle`` byte for byte. Verifies the result and
    removes a partial output on failure."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(bundle, mode="r:gz") as old, out_path.open("xb") as raw, \
                gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, \
                tarfile.open(fileobj=zipped, mode="w") as new:
            meta = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            new.addfile(_entry("manifest.json", meta), io.BytesIO(meta))
            for item in manifest["files"]:
                name = item["path"]
                if name in new_content:
                    content = new_content[name]
                else:
                    source = old.extractfile(name)
                    if source is None:
                        raise ValueError(f"unreadable base source: {name}")
                    content = source.read()
                new.addfile(_entry(name, content), io.BytesIO(content))
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
    return verify(out_path)


ARCHIVE_PREFIX = "backfill/cache/"


def add_archives(bundle: Path, archives: Path, out_path: Path) -> dict:
    """Add new historical-archive cache files to a verified frozen bundle.

    ``archives`` is a collector cache root holding ``archives/<state>/...``
    (the layout ``backfill.state_archives`` and the NE collector write); each
    file becomes ``backfill/cache/archives/<state>/...``. The base bundle is
    verified, every base member is copied byte for byte, and only paths the
    bundle does not already hold are added: an existing member is never
    replaced. The manifest lists the files and, cumulatively, the added paths
    under ``archive_additions``. The output is written deterministically and
    verified. Overlay-raw cannot do this (it only replaces a raw CSV), and
    create() rebuilds from a workdir, dropping the ``agency/`` artifacts.
    """
    bundle, archives, out_path = Path(bundle), Path(archives), Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"source bundle already exists: {out_path}")
    if archives.is_symlink() or not archives.is_dir():
        raise ValueError(f"invalid archive directory: {archives}")
    manifest = verify(bundle)
    existing = {item["path"] for item in manifest["files"]}
    additions: dict[str, bytes] = {}
    for path in sorted(archives.rglob("*"), key=lambda p: p.as_posix()):
        if path.is_symlink():
            raise ValueError(f"source symlink is not allowed: {path}")
        if not path.is_file():
            continue
        rel = path.relative_to(archives).as_posix()
        parts = PurePosixPath(rel).parts
        if len(parts) < 3 or parts[0] != "archives" or ".." in parts:
            raise ValueError(f"not an archive cache file (archives/<state>/...): {rel}")
        name = ARCHIVE_PREFIX + rel
        if name in existing:
            raise ValueError(f"bundle already has {name}; additions never replace a member")
        additions[name] = path.read_bytes()
    if not additions:
        raise ValueError(f"no archive files to add under {archives}")
    files = {item["path"]: item for item in json.loads(json.dumps(manifest["files"]))}
    for name, content in additions.items():
        files[name] = {"path": name, "size": len(content),
                       "sha256": hashlib.sha256(content).hexdigest()}
    manifest = json.loads(json.dumps(manifest))
    manifest["files"] = [files[name] for name in sorted(files)]
    manifest["archive_additions"] = sorted(
        set(manifest.get("archive_additions", [])) | set(additions))
    return _rewrite(bundle, manifest, additions, out_path)


AGENCY_NAME = re.compile(r"[a-z]{2}_[a-z0-9_]+")


def add_agency(bundle: Path, artifacts: Path, name: str, out_path: Path) -> dict:
    """Add one new pinned agency artifact directory as ``agency/<name>/``.

    ``artifacts`` is a flat directory (a dated ``data/source_snapshots`` path)
    holding ``manifest.json`` and the files it pins. The base bundle is
    verified and copied byte for byte; the call refuses a name the bundle
    already holds, so an existing ``agency/`` table is never replaced or
    extended. The state projector named by the offline rebuild validates the
    manifest and checksums when it replays the bundle. The manifest records
    the added directories, cumulatively, under ``agency_additions``.
    """
    bundle, artifacts, out_path = Path(bundle), Path(artifacts), Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"source bundle already exists: {out_path}")
    if not AGENCY_NAME.fullmatch(name):
        raise ValueError(f"invalid agency artifact name: {name!r}")
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError(f"invalid agency artifact directory: {artifacts}")
    manifest = verify(bundle)
    prefix = f"agency/{name}/"
    if any(item["path"].startswith(prefix) for item in manifest["files"]):
        raise ValueError(f"bundle already has {prefix}; additions never replace a member")
    additions: dict[str, bytes] = {}
    for path in sorted(artifacts.iterdir(), key=lambda p: p.name):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"agency artifacts must be regular files: {path}")
        additions[prefix + path.name] = path.read_bytes()
    if prefix + "manifest.json" not in additions:
        raise ValueError(f"agency artifact directory lacks manifest.json: {artifacts}")
    files = {item["path"]: item for item in json.loads(json.dumps(manifest["files"]))}
    for member, content in additions.items():
        files[member] = {"path": member, "size": len(content),
                         "sha256": hashlib.sha256(content).hexdigest()}
    manifest = json.loads(json.dumps(manifest))
    manifest["files"] = [files[member] for member in sorted(files)]
    manifest["agency_additions"] = sorted(
        set(manifest.get("agency_additions", [])) | {prefix.rstrip("/")})
    return _rewrite(bundle, manifest, additions, out_path)


def extract(bundle: Path, destination: Path) -> dict:
    """Verify first, then extract into a new directory without overwriting."""
    manifest = verify(bundle)
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"source destination already exists: {destination}")
    destination.mkdir(parents=True)
    with tarfile.open(bundle, mode="r:gz") as archive:
        for member in archive.getmembers()[1:]:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"unreadable source: {member.name}")
            with target.open("xb") as output:
                shutil.copyfileobj(source, output)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create")
    make.add_argument("--workdir", type=Path, default=Path("workdir"))
    make.add_argument("--out", type=Path, required=True)
    make.add_argument("--raw-overlay", type=Path,
                      help="Directory of freshly captured two-letter state CSVs to replace stale raw inputs")
    make.add_argument("--agency-artifacts", type=Path,
                      help="Verified Louisiana manifest and annual PDFs to include under agency/la")
    make.add_argument("--ia-artifacts", type=Path,
                      help="Verified Iowa manifest and event-log workbook to include under agency/ia")
    make.add_argument("--ny-artifacts", type=Path,
                      help="Verified New York dashboard CSVs to include under agency/ny")
    make.add_argument("--tx-artifacts", type=Path,
                      help="Verified Texas annual workbooks to include under agency/tx")
    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    overlay = sub.add_parser("overlay-raw")
    overlay.add_argument("bundle", type=Path)
    overlay.add_argument("--raw-file", type=Path, required=True)
    overlay.add_argument("--out", type=Path, required=True)
    overlay.add_argument("--evidence-archive", type=Path)
    archive_add = sub.add_parser("add-archives")
    archive_add.add_argument("bundle", type=Path)
    archive_add.add_argument("--archives", type=Path, required=True,
                             help="Cache root holding archives/<state>/... files to add")
    archive_add.add_argument("--out", type=Path, required=True)
    agency_add = sub.add_parser("add-agency")
    agency_add.add_argument("bundle", type=Path)
    agency_add.add_argument("--artifacts", type=Path, required=True,
                            help="Pinned directory holding manifest.json and its files")
    agency_add.add_argument("--name", required=True,
                            help="Bundle directory name under agency/ (e.g. ky_archive)")
    agency_add.add_argument("--out", type=Path, required=True)
    unpack = sub.add_parser("extract")
    unpack.add_argument("bundle", type=Path)
    unpack.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = (
        create(args.workdir, args.out, args.raw_overlay,
               args.agency_artifacts, args.ia_artifacts,
               args.ny_artifacts, args.tx_artifacts) if args.command == "create"
        else overlay_raw_bundle(args.bundle, args.raw_file, args.out, args.evidence_archive)
        if args.command == "overlay-raw"
        else add_archives(args.bundle, args.archives, args.out) if args.command == "add-archives"
        else add_agency(args.bundle, args.artifacts, args.name, args.out)
        if args.command == "add-agency"
        else extract(args.bundle, args.out) if args.command == "extract"
        else verify(args.bundle)
    )
    print(json.dumps({
        "format": result["format"], "files": len(result["files"]),
        "bytes": sum(item["size"] for item in result["files"]),
    }, sort_keys=True))
