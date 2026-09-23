"""Freeze and verify the local source inputs needed for an offline rebuild.

The bundle is an input archive, not a database backup.  It records the exact
bytes of rolling raw CSVs, historical backfills, and cached agency artifacts
so a later rebuild is not dependent on whatever a state serves that day.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath

REQUIRED = (
    "raw", "backfill/raw", "backfill/bln_integrated.csv",
    "backfill/cache/archives", "cache/sc",
)
OPTIONAL = (
    "cache/il_reports", "cache/ga/ga_historical.csv",
    "cache/tn/tn_historical.csv",
)


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


def _rebuild_policy(db_path: Path) -> bytes:
    """Freeze historic overlap decisions, not canonical notice payloads."""
    if not db_path.is_file():
        raise FileNotFoundError(f"baseline database missing: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        keys: list[str] = []
        source_urls: dict[str, str] = {}
        states: dict[str, dict[str, int]] = {}
        for key, state, url, jobs in conn.execute(
            "SELECT dedupe_key, state, source_url, employees_affected "
            "FROM notices ORDER BY dedupe_key"
        ):
            keys.append(key)
            if state in {"CA", "NY"} and url:
                source_urls[key] = url
            item = states.setdefault(state, {"notices": 0, "workers": 0})
            item["notices"] += 1
            item["workers"] += jobs or 0
    finally:
        conn.close()
    return (json.dumps({
        "format": "warn-rebuild-policy-v1", "accepted_keys": keys,
        "archive_source_urls": source_urls, "baseline_states": states,
    }, sort_keys=True, separators=(",", ":")) + "\n").encode()


def create(
    workdir: Path, out_path: Path, policy_db: Path | None = None,
    raw_overlay: Path | None = None,
    agency_artifacts: Path | None = None,
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
        if set(additional) & set(files):
            raise ValueError("agency artifact collides with workdir input")
        files = sorted(set(files) | set(additional), key=lambda p: p.as_posix())

    def source_bytes(rel: Path) -> bytes:
        return overrides.get(rel, additional.get(rel, workdir / rel)).read_bytes()

    policy = _rebuild_policy(Path(policy_db)) if policy_db is not None else None
    manifest = {"format": "warn-source-bundle-v1", "files": [],
                "raw_overrides": sorted(rel.as_posix() for rel in overrides)}
    for rel in files:
        data = source_bytes(rel)
        manifest["files"].append({
            "path": rel.as_posix(), "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    if policy is not None:
        manifest["files"].append({
            "path": "rebuild_policy.json", "size": len(policy),
            "sha256": hashlib.sha256(policy).hexdigest(),
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
            if policy is not None:
                archive.addfile(_entry("rebuild_policy.json", policy), io.BytesIO(policy))
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
    make.add_argument("--policy-db", type=Path,
                      help="Freeze accepted historical keys and archive URLs from this DB")
    make.add_argument("--raw-overlay", type=Path,
                      help="Directory of freshly captured two-letter state CSVs to replace stale raw inputs")
    make.add_argument("--agency-artifacts", type=Path,
                      help="Verified Louisiana manifest and annual PDFs to include under agency/la")
    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    unpack = sub.add_parser("extract")
    unpack.add_argument("bundle", type=Path)
    unpack.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = (
        create(args.workdir, args.out, args.policy_db, args.raw_overlay,
               args.agency_artifacts) if args.command == "create"
        else extract(args.bundle, args.out) if args.command == "extract"
        else verify(args.bundle)
    )
    print(json.dumps({
        "format": result["format"], "files": len(result["files"]),
        "bytes": sum(item["size"] for item in result["files"]),
    }, sort_keys=True))
