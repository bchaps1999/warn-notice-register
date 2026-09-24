"""Read pinned New York dashboard rows as source evidence, without matching notices.

The Tableau CSV repeats the affected-worker header. ``csv.DictReader`` would
silently discard one value, so the original header and every cell are retained.
These rows are observations; amendments and multiple sites require review.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path


YEARS = (2022, 2023, 2024)
HEADERS = (
    "Business Legal Name", "Date Layoff/Closure Starts",
    "Date of WARN Notice ", "Date Posted  ", "Impacted Site Address",
    "Impacted Site County", "Layoff or Closure?",
    "Permanent or Temporary Layoff?", "Reason for Layoff/Closure   ",
    "Index", "Number of Affected Workers ", "Number of Affected Workers ",
)


def _date(value: str, label: str, row_number: int) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid New York {label} at row {row_number}: {value!r}") from exc


def verified_extra_paths(artifact_dir: Path, manifest: dict) -> list[Path]:
    """Verify manifest-listed filings and reviewed decisions for source replay."""
    paths: list[Path] = []
    if not isinstance(manifest.get("documents", []), list):
        raise ValueError("invalid New York document list")
    entries = list(manifest.get("documents", []))
    decision = manifest.get("reviewed_decisions")
    if decision is not None:
        entries.append(decision)
    event_decision = manifest.get("reviewed_event_decisions")
    if event_decision is not None:
        entries.append(event_decision)
    seen: set[str] = set()
    for item in entries:
        if not isinstance(item, dict):
            raise ValueError("invalid New York extra artifact")
        name = item.get("path")
        if (not isinstance(name, str) or not name or name in seen
                or Path(name).is_absolute() or ".." in Path(name).parts
                or "\\" in name):
            raise ValueError(f"invalid New York extra artifact path: {name!r}")
        seen.add(name)
        path = artifact_dir / name
        root = artifact_dir.resolve()
        inner_parents = [artifact_dir.joinpath(*Path(name).parts[:index])
                         for index in range(1, len(Path(name).parts))]
        if (path.is_symlink() or not path.resolve().is_relative_to(root)
                or any(parent.is_symlink() for parent in inner_parents)
                or not path.is_file()):
            raise ValueError(f"invalid New York extra artifact: {path}")
        content = path.read_bytes()
        if len(content) != item.get("bytes") or hashlib.sha256(content).hexdigest() != item.get("sha256"):
            raise ValueError(f"New York source checksum mismatch: {path}")
        paths.append(Path(name))
    return paths


def read_artifacts(artifact_dir: Path) -> list[dict]:
    """Verify the frozen CSVs and return lossless, artifact-scoped row records."""
    artifact_dir = Path(artifact_dir)
    manifest = json.loads((artifact_dir / "manifest.json").read_text())
    if manifest.get("format") != "warn-ny-source-artifacts-v1":
        raise ValueError("unsupported New York source manifest")
    artifacts = manifest.get("artifacts")
    expected_names = {f"ny_warn_{year}.csv" for year in YEARS}
    if (not isinstance(artifacts, list) or len(artifacts) != len(YEARS)
            or {item.get("path") for item in artifacts} != expected_names):
        raise ValueError("New York manifest must list the three annual CSVs")
    verified_extra_paths(artifact_dir, manifest)

    result = []
    for item in sorted(artifacts, key=lambda value: value["path"]):
        name = item["path"]
        year = int(name.removeprefix("ny_warn_").removesuffix(".csv"))
        path = artifact_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"invalid New York artifact: {path}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != item.get("bytes") or digest != item.get("sha256"):
            raise ValueError(f"New York source checksum mismatch: {path}")
        if item.get("year") != year or item.get("headers") != list(HEADERS):
            raise ValueError(f"New York manifest schema mismatch: {name}")
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.reader(stream)
            if tuple(next(reader, ())) != HEADERS:
                raise ValueError(f"New York CSV header changed: {name}")
            for row_number, cells in enumerate(reader, start=2):
                if len(cells) != len(HEADERS):
                    raise ValueError(f"New York CSV row width changed: {name}:{row_number}")
                if cells[10] != cells[11]:
                    raise ValueError(f"New York worker columns disagree: {name}:{row_number}")
                notice = _date(cells[2], "notice date", row_number)
                if notice is None or not notice.startswith(str(year)):
                    raise ValueError(f"New York CSV notice year changed: {name}:{row_number}")
                effective = _date(cells[1], "layoff start", row_number)
                posted = _date(cells[3], "posted date", row_number)
                if not cells[0] or not cells[9]:
                    raise ValueError(f"incomplete New York CSV row: {name}:{row_number}")
                raw = json.dumps(cells, ensure_ascii=False, separators=(",", ":"))
                row_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                result.append({
                    "source_row_id": f"agency/ny/{name}:sha256:{digest}:row:{row_number}:sha256:{row_sha}",
                    "artifact": f"agency/ny/{name}",
                    "artifact_sha256": digest,
                    "source_url": item["source_url"],
                    "row_number": row_number,
                    "row_sha256": row_sha,
                    "headers": list(HEADERS),
                    "raw_cells": cells,
                    "company": cells[0],
                    "effective_date": effective,
                    "notice_date": notice,
                    "posted_date": posted,
                    "address": cells[4],
                    "county": cells[5],
                    "event_type": cells[6],
                    "permanence": cells[7],
                    "reason": cells[8],
                    "index": cells[9],
                    "workers": cells[10],
                    "workers_duplicate_column": cells[11],
                })
        if sum(row["artifact"] == f"agency/ny/{name}" for row in result) != item.get("data_rows"):
            raise ValueError(f"New York CSV row count changed: {name}")
    return result
