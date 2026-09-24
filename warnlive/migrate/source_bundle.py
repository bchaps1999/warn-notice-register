"""Freeze and verify the local source inputs needed for an offline rebuild.

The bundle is an input archive, not a database backup.  It records the exact
bytes of rolling raw CSVs, historical backfills, and cached agency artifacts
so a later rebuild is not dependent on whatever a state serves that day.
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
import sqlite3
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
    ia_artifacts: Path | None = None,
    ny_artifacts: Path | None = None,
    tx_artifacts: Path | None = None,
) -> dict:
    """Create a deterministic bundle; never overwrite an existing snapshot."""
    if policy_db is not None:
        raise ValueError("old-database overlap policy is retired for agency-only builds")
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


def agency_only(bundle: Path, out_path: Path) -> dict:
    """Derive an immutable agency-input bundle from a verified older bundle."""
    original = verify(bundle)
    out_path = Path(out_path)
    if out_path.exists():
        raise FileExistsError(f"source bundle already exists: {out_path}")
    overrides: dict[str, bytes] = {}
    excluded = {
        "backfill/bln_integrated.csv", "rebuild_policy.json",
        "cache/ga/ga_historical.csv", "cache/tn/tn_historical.csv",
        "raw/ia.csv", "raw/ky.csv", "raw/or.csv",
    }
    ny_manifest_path = "agency/ny/manifest.json"
    with tarfile.open(bundle, mode="r:gz") as old:
        ny_file = old.extractfile(ny_manifest_path) if ny_manifest_path in old.getnames() else None
        if ny_file is not None:
            ny_manifest = json.load(ny_file)
            for key in ("reviewed_decisions", "reviewed_event_decisions"):
                decision = ny_manifest.pop(key, None)
                if decision is not None:
                    excluded.add(f"agency/ny/{decision['path']}")
            overrides[ny_manifest_path] = (
                json.dumps(ny_manifest, sort_keys=True, indent=2) + "\n"
            ).encode()
        tx_manifest_path = "agency/tx/manifest.json"
        if tx_manifest_path in old.getnames():
            tx_manifest = json.load(old.extractfile(tx_manifest_path))
            agency_rows = sum(item["data_rows"] for item in tx_manifest["artifacts"])
            raw_rows = list(csv.reader(io.StringIO(
                old.extractfile("raw/tx.csv").read().decode("utf-8-sig"), newline=""
            )))
            if len(raw_rows) <= agency_rows + 1 or any(
                not row or not row[0].startswith("20") or int(row[0][:4]) < 2020
                for row in raw_rows[1:agency_rows + 1]
            ) or any(
                not row or not row[0][:4].isdigit() or int(row[0][:4]) >= 2020
                for row in raw_rows[agency_rows + 1:]
            ):
                raise ValueError("Texas agency/historical raw boundary changed")
            output = io.StringIO(newline="")
            csv.writer(output).writerows(raw_rows[:agency_rows + 1])
            tx_csv = output.getvalue().encode()
            overrides["raw/tx.csv"] = tx_csv
            tx_manifest["raw_csv_bytes"] = len(tx_csv)
            tx_manifest["raw_csv_sha256"] = hashlib.sha256(tx_csv).hexdigest()
            overrides[tx_manifest_path] = (
                json.dumps(tx_manifest, sort_keys=True, indent=2) + "\n"
            ).encode()
        if "raw/oh.csv" in old.getnames():
            reader = csv.DictReader(io.StringIO(
                old.extractfile("raw/oh.csv").read().decode("utf-8-sig"), newline=""
            ))
            rows = list(reader)
            current = [row for row in rows if row.get("URL")]
            if not current or len(current) == len(rows) or rows[:len(current)] != current:
                raise ValueError("Ohio agency/historical raw boundary changed")
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=reader.fieldnames)
            writer.writeheader()
            writer.writerows(current)
            overrides["raw/oh.csv"] = output.getvalue().encode()
        for postal in ("ga", "tn"):
            raw_path = f"raw/{postal}.csv"
            history_path = f"cache/{postal}/{postal}_historical.csv"
            if raw_path not in old.getnames():
                continue
            if history_path not in old.getnames():
                raise ValueError(f"{postal.upper()} historical cache missing for boundary check")
            raw_reader = csv.DictReader(io.StringIO(
                old.extractfile(raw_path).read().decode("utf-8-sig"), newline=""
            ))
            raw_rows = list(raw_reader)
            history_text = old.extractfile(history_path).read().decode("utf-8-sig")
            historical = list(csv.DictReader(
                history_text.splitlines() if postal == "tn"
                else io.StringIO(history_text, newline="")
            ))
            cutoff = len(raw_rows) - len(historical)
            if cutoff <= 0 or not historical:
                raise ValueError(f"{postal.upper()} agency/historical raw boundary changed")
            if postal == "tn":
                matches = all(raw == old_row for raw, old_row in
                              zip(raw_rows[cutoff:], historical, strict=True))
            else:
                columns = {
                    "ID": "GA WARN ID", "Company Name": "Company Name",
                    "City": "First Location Address", "ZIP": "Zip Code",
                    "County": "County", "Est. Impact": "Total Number of Affected Employees",
                    "LWDA": "LWDA", "Separation Date": "First Date of Separation",
                }
                matches = all(
                    all(raw.get(output) == old_row.get(source)
                        for source, output in columns.items())
                    and all(not value for key, value in raw.items()
                            if key not in columns.values())
                    for raw, old_row in zip(raw_rows[cutoff:], historical, strict=True)
                )
            if not matches:
                raise ValueError(f"{postal.upper()} historical rows differ from pinned cache")
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=raw_reader.fieldnames)
            writer.writeheader()
            writer.writerows(raw_rows[:cutoff])
            overrides[raw_path] = output.getvalue().encode()
    keep = [item for item in original["files"] if
            item["path"] != "backfill/bln_integrated.csv"
            and not item["path"].startswith("backfill/raw/")
            and item["path"] not in excluded]
    keep = [{**item, "size": len(overrides[item["path"]]),
             "sha256": hashlib.sha256(overrides[item["path"]]).hexdigest()}
            if item["path"] in overrides else item for item in keep]
    manifest = {**original, "files": keep, "admission_inputs": "agency-only-v1"}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(bundle, mode="r:gz") as old, out_path.open("xb") as raw, \
                gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, \
                tarfile.open(fileobj=zipped, mode="w") as new:
            meta = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
            new.addfile(_entry("manifest.json", meta), io.BytesIO(meta))
            for item in keep:
                if item["path"] in overrides:
                    data = overrides[item["path"]]
                    new.addfile(_entry(item["path"], data), io.BytesIO(data))
                    continue
                source = old.extractfile(item["path"])
                if source is None:
                    raise ValueError(f"unreadable source: {item['path']}")
                info = _entry(item["path"], b"")
                info.size = item["size"]
                new.addfile(info, source)
    except BaseException:
        out_path.unlink(missing_ok=True)
        raise
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
    make.add_argument("--ia-artifacts", type=Path,
                      help="Verified Iowa manifest and event-log workbook to include under agency/ia")
    make.add_argument("--ny-artifacts", type=Path,
                      help="Verified New York dashboard CSVs to include under agency/ny")
    make.add_argument("--tx-artifacts", type=Path,
                      help="Verified Texas annual workbooks to include under agency/tx")
    check = sub.add_parser("verify")
    check.add_argument("bundle", type=Path)
    agency = sub.add_parser("agency-only")
    agency.add_argument("bundle", type=Path)
    agency.add_argument("--out", type=Path, required=True)
    unpack = sub.add_parser("extract")
    unpack.add_argument("bundle", type=Path)
    unpack.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = (
        create(args.workdir, args.out, args.policy_db, args.raw_overlay,
               args.agency_artifacts, args.ia_artifacts,
               args.ny_artifacts, args.tx_artifacts) if args.command == "create"
        else agency_only(args.bundle, args.out) if args.command == "agency-only"
        else extract(args.bundle, args.out) if args.command == "extract"
        else verify(args.bundle)
    )
    print(json.dumps({
        "format": result["format"], "files": len(result["files"]),
        "bytes": sum(item["size"] for item in result["files"]),
    }, sort_keys=True))
