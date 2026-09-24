"""Fail closed when published exports and site data disagree with the database."""

from __future__ import annotations

import argparse
import csv
import filecmp
import json
import sqlite3
import tempfile
from pathlib import Path

from warnlive.registry import load_registry
from warnlive.store.export import export_csvs
from warnlive.store.links import export_links_csv
from warnlive.store.site_export import build_site


def _files(root: Path, suffix: str) -> set[Path]:
    return {path.relative_to(root) for path in root.rglob(f"*{suffix}") if path.is_file()}


def _compare_tree(expected: Path, actual: Path, suffix: str, label: str) -> None:
    expected_paths = _files(expected, suffix)
    actual_paths = _files(actual, suffix)
    if expected_paths != actual_paths:
        missing = sorted(str(path) for path in expected_paths - actual_paths)
        extra = sorted(str(path) for path in actual_paths - expected_paths)
        raise ValueError(f"{label} file set disagrees with database: missing={missing}, extra={extra}")
    for path in sorted(expected_paths):
        if not filecmp.cmp(expected / path, actual / path, shallow=False):
            raise ValueError(f"{label} content disagrees with database: {path}")


def verify(db_path: Path, exports: Path, site_data: Path | None = None) -> dict:
    """Rebuild publication artifacts and compare their complete contents.

    The exporter is the publication contract: it includes source columns,
    derived identity/geography fields, state cuts, and source observations.
    Rebuilding with the same pinned reference files detects a stale or altered
    artifact even when counts and a handful of core columns still agree.
    """
    db_path, exports = Path(db_path), Path(exports)
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("database integrity check failed")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("database foreign key check failed")

        registry = load_registry()
        publishable = sorted(cfg.postal for cfg in registry.all()
                             if cfg.status in {"active", "archive"})
        with tempfile.TemporaryDirectory(prefix="warn-release-verify-") as temporary:
            root = Path(temporary)
            expected_exports = root / "exports"
            export_csvs(conn, expected_exports, publishable)
            export_links_csv(conn, expected_exports / "notice_links.csv")
            _compare_tree(expected_exports, exports, ".csv", "CSV export")

            if site_data is not None:
                site_data = Path(site_data)
                meta = json.loads((site_data / "meta.json").read_text())
                as_of = meta["built_at"][:10]
                expected_site = root / "site"
                build_site(conn, registry, expected_site, as_of=as_of,
                           built_at=meta["built_at"])
                _compare_tree(expected_site, site_data, ".json", "site data")

        with (exports / "warn_notices.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        return {
            "notices": len(rows),
            "workers": sum(int(row["employees_affected"]) for row in rows
                           if row["employees_affected"]),
            "states": len({row["state"] for row in rows}),
            "source_observations": conn.execute(
                "SELECT COUNT(*) FROM source_observations"
            ).fetchone()[0],
        }
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/warn.sqlite"))
    parser.add_argument("--exports", type=Path, default=Path("data/exports"))
    parser.add_argument("--site-data", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.db, args.exports, args.site_data), sort_keys=True))
