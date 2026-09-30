"""Read-only Nebraska key transition and report separation for v1.2.

Two NE changes alter published identities:

* location now comes from ``City``, else the filed ``Location`` cell
  (normalize.custom.ne). Rows with a City keep their key; rows without one
  (every 2020+ page row, a few 2013-2016 year-report rows) get a new key.
* rows of NDOL's layoff/closure report (LayoffAndClosureReportData) are held
  as ``ne_layoff_closure_report_not_warn``. A published notice whose every
  source row is a layoff/closure row is retired from the register.

Inputs: a published database (read-only), a tagged NE raw CSV written by
``fetch.patches.ne`` (``source_report`` column), and optionally a cache
holding the pinned 2020-2022 NDOL capture (backfill.state_archives
.fetch_ne_dol, read cache-only). Each source row is normalized with the new
transformer; its old key is the same record keyed with upstream's
City-only location. Output: one CSV row per published NE notice (keep,
rekey, retire, or unmatched) plus one per 2020-2022 backfill row, and a
JSON summary. Writes only the named output paths; no network access.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tempfile
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from warnlive.backfill import state_archives
from warnlive.normalize.custom.ne import LAYOFF_CLOSURE_REPORT, WARN_REPORT
from warnlive.normalize.engine import _clean_text, _dedupe_key, normalize_file

FIELDS = (
    "published_notice_id", "old_dedupe_key", "new_dedupe_key", "action",
    "source_reports", "notice_date", "employer_name", "old_location", "new_location",
    "source_rows", "matched_warn_report_rows", "evidence",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _old_key(rec: dict) -> str:
    """The key upstream's transformer (location = City) gave this record."""
    raw = json.loads(rec["raw_extra"])
    return _dedupe_key(dict(rec, location=_clean_text(raw.get("City"))))


def source_records(raw_csv: Path) -> list[dict]:
    """Every tagged row normalized with the new transformer, hold ignored.

    The report tag is blanked while normalizing so layoff/closure rows are
    transformed rather than held; keys do not depend on the tag.
    """
    with raw_csv.open(newline="") as fh:
        reader = csv.DictReader(fh)
        header, rows = reader.fieldnames, list(reader)
    if "source_report" not in (header or []):
        raise ValueError("NE transition needs a tagged raw CSV (source_report column)")
    out = []
    for report in (WARN_REPORT, LAYOFF_CLOSURE_REPORT):
        group = [row for row in rows if row["source_report"] == report]
        with tempfile.TemporaryDirectory() as tmp:
            with open(Path(tmp) / "ne.csv", "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=header)
                writer.writeheader()
                writer.writerows(dict(row, source_report="") for row in group)
            result = normalize_file("ne", Path(tmp), None, observed_at="2026-09-30")
        if result.failures:
            raise ValueError(f"NE {report}: {len(result.failures)} rows failed: "
                             f"{result.failure_examples}")
        for rec in result.records:
            raw = json.loads(rec["raw_extra"])
            rec["report"] = report
            rec["source_row"] = (f"{raw['ndol_source_page']}#row{raw['ndol_page_row']}")
            rec["matched_warn_row"] = raw.get("ndol_matched_warn_row") or ""
            rec["old_key"] = _old_key(rec)
            out.append(rec)
    return out


def _published(db: Path) -> list[sqlite3.Row]:
    uri = f"file:{quote(db.resolve().as_posix(), safe='/:')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT id, dedupe_key, employer_name, location, notice_date "
            "FROM notices WHERE state = 'NE' ORDER BY notice_date, id"
        ).fetchall()
    finally:
        conn.close()


def transition(db: Path, raw_csv: Path, archive_cache: Path | None = None) -> tuple[list[dict], dict]:
    records = source_records(raw_csv)
    by_old: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_old[rec["old_key"]].append(rec)
    rows: list[dict] = []
    for notice in _published(db):
        recs = by_old.get(notice["dedupe_key"], [])
        reports = sorted({rec["report"] for rec in recs})
        warn = [rec for rec in recs if rec["report"] == WARN_REPORT]
        new_keys = sorted({rec["dedupe_key"] for rec in warn})
        if not recs:
            action, evidence = "unmatched", "no current source row has this key"
        elif not warn:
            action = "retire"
            evidence = "every source row is in the layoff/closure report, not the WARN report"
        elif len(new_keys) > 1:
            action = "split"
            evidence = "WARN rows sharing the old key now carry distinct locations"
        elif new_keys == [notice["dedupe_key"]]:
            action, evidence = "keep", "key unchanged"
        else:
            action = "rekey"
            evidence = "City blank; location now from the filed Location cell"
        if warn and len(reports) > 1:
            evidence += "; layoff/closure rows with this key are held, WARN row kept"
        rows.append({
            "published_notice_id": notice["id"], "old_dedupe_key": notice["dedupe_key"],
            "new_dedupe_key": ";".join(new_keys) if warn else "",
            "action": action, "source_reports": ";".join(reports),
            "notice_date": notice["notice_date"], "employer_name": notice["employer_name"],
            "old_location": notice["location"] or "",
            "new_location": ";".join(sorted({rec["location"] or "" for rec in warn})),
            "source_rows": ";".join(sorted(rec["source_row"] for rec in recs)),
            "matched_warn_report_rows": ";".join(
                sorted({rec["matched_warn_row"] for rec in recs if rec["matched_warn_row"]})
            ),
            "evidence": evidence,
        })
    published_keys = {row["old_dedupe_key"] for row in rows}
    unpublished_warn = [rec for rec in records if rec["report"] == WARN_REPORT
                        and rec["old_key"] not in published_keys]
    backfill = _backfill_rows(archive_cache) if archive_cache else []
    rows.extend(backfill)
    summary = {
        "raw_csv": str(raw_csv), "raw_csv_sha256": _sha(raw_csv),
        "database": str(db), "database_sha256": _sha(db),
        "source_rows": {report: sum(rec["report"] == report for rec in records)
                        for report in (WARN_REPORT, LAYOFF_CLOSURE_REPORT)},
        "layoff_closure_rows_matching_warn_row": sum(
            1 for rec in records if rec["matched_warn_row"]),
        "published_notices": sum(1 for row in rows if row["published_notice_id"] != ""),
        "actions": {},
        "warn_rows_not_published": [
            {"source_row": rec["source_row"], "employer_name": rec["employer_name"],
             "notice_date": rec["notice_date"], "new_dedupe_key": rec["dedupe_key"]}
            for rec in unpublished_warn
        ],
    }
    for row in rows:
        summary["actions"][row["action"]] = summary["actions"].get(row["action"], 0) + 1
    summary["retired_dedupe_keys"] = sorted(
        row["old_dedupe_key"] for row in rows if row["action"] == "retire")
    return rows, summary


def _backfill_rows(cache: Path) -> list[dict]:
    """2020-2022 page rows (not yet published): City-based vs new key."""
    def cached_only(url, dest):
        return dest.read_bytes() if dest.exists() else None

    with patch.object(state_archives, "_download", side_effect=cached_only):
        records = state_archives.fetch_ne_dol(cache)
    out = []
    for rec in records:
        raw = json.loads(rec["raw_extra"])
        old = _old_key(rec)
        out.append({
            "published_notice_id": "", "old_dedupe_key": old,
            "new_dedupe_key": rec["dedupe_key"], "action": "backfill_new_key",
            "source_reports": WARN_REPORT, "notice_date": rec["notice_date"],
            "employer_name": rec["employer_name"], "old_location": "",
            "new_location": rec["location"] or "",
            "source_rows": f"{raw['ndol_source_page']}#row{raw['ndol_page_row']}",
            "matched_warn_report_rows": "",
            "evidence": "2020-2022 page row (pinned capture); not published; "
                        "old key is the City-based key",
        })
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True, help="tagged ne.csv")
    parser.add_argument("--archive-cache", type=Path)
    parser.add_argument("--out", type=Path, required=True, help="transition CSV")
    parser.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)
    rows, summary = transition(args.db, args.raw, args.archive_cache)
    with args.out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
