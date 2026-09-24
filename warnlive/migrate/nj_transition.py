"""Read-only old-to-new NJ observation disposition after a date-role rebuild.

The old database nominations come from a frozen ``nj_reconcile`` report.
They are comparison pointers, never evidence that two rows share a filing.
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
from urllib.parse import quote


FIELDS = (
    "raw_row_sha256", "current_occurrences", "source_disposition", "parse_error",
    "old_notice_ids", "old_dedupe_keys", "new_notice_id", "new_dedupe_key",
    "new_source_identity", "admission_status", "exception_reasons",
)
FANOUT_FIELDS = (
    "old_notice_id", "new_observations", "new_notice_ids", "raw_row_hashes",
    "companies", "cities", "posting_months", "effective_source_texts",
    "worker_source_texts", "review_status",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _new_candidate(path: Path) -> tuple[dict[str, list[dict]], str]:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"
    source = sqlite3.connect(uri, uri=True)
    with tempfile.TemporaryDirectory(prefix="warn-nj-transition-") as directory:
        snapshot_path = Path(directory) / "candidate.sqlite"
        snapshot = sqlite3.connect(snapshot_path)
        try:
            source.backup(snapshot)
        finally:
            snapshot.close()
            source.close()
        digest = _sha(snapshot_path)
        conn = sqlite3.connect(snapshot_path)
        conn.row_factory = sqlite3.Row
        mapped: dict[str, list[dict]] = defaultdict(list)
        try:
            conn.execute("PRAGMA query_only=ON")
            for row in conn.execute(
                "SELECT id,dedupe_key,source_identity,source_details FROM notices "
                "WHERE state='NJ' ORDER BY id"
            ):
                details = json.loads(row["source_details"] or "{}")
                sha = details.get("source_row_sha256")
                if not isinstance(sha, str) or len(sha) != 64:
                    raise ValueError(f"NJ notice {row['id']} lacks a source row hash")
                mapped[sha].append({"id": row["id"], "dedupe_key": row["dedupe_key"],
                                    "source_identity": row["source_identity"]})
        finally:
            conn.close()
    return mapped, digest


def build(correspondence_dir: Path, new_db: Path, exceptions_path: Path,
          out_dir: Path) -> dict:
    correspondence_dir, new_db = Path(correspondence_dir), Path(new_db)
    csv_path = correspondence_dir / "nj_source_correspondence.csv"
    source_manifest = json.loads((correspondence_dir / "nj_source_correspondence.json").read_text())
    if source_manifest.get("csv_sha256") != _sha(csv_path):
        raise ValueError("NJ correspondence CSV drift")
    with csv_path.open(newline="", encoding="utf-8") as stream:
        source_rows = [row for row in csv.DictReader(stream) if row["artifact"] == "raw/nj.csv"]
    by_sha: dict[str, list[dict]] = defaultdict(list)
    for row in source_rows:
        by_sha[row["raw_row_sha256"]].append(row)
    reasons: dict[int, list[str]] = defaultdict(list)
    with Path(exceptions_path).open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            if item.get("origin") == "raw/nj.csv" and item.get("prepared_row") is not None:
                reasons[int(item["prepared_row"])].append(item["reason"])
    new_rows, new_sha = _new_candidate(new_db)
    extra = sorted(set(new_rows) - set(by_sha))
    if extra:
        raise ValueError(f"new NJ candidate has {len(extra)} rows absent from current raw source")
    output = []
    raw_by_sha = {}
    for sha, rows in sorted(by_sha.items()):
        raw_by_sha[sha] = json.loads(rows[0].get("raw_json") or "{}")
        admitted = new_rows.get(sha, [])
        if len(admitted) > 1:
            raise ValueError(f"one NJ raw row maps to multiple new notices: {sha}")
        old_versions = [item for row in rows for item in json.loads(row["candidate_versions"])]
        parse_errors = sorted({row["parse_error"] for row in rows if row["parse_error"]})
        row_reasons = sorted({reason for row in rows
                              for reason in reasons.get(int(row["prepared_row"]), [])})
        if admitted:
            status = "admitted"
        elif any(not reasons.get(int(row["prepared_row"])) for row in rows):
            status = "unaccounted"
        elif "parse_failure" in row_reasons:
            status = "parse_failure"
        elif row_reasons:
            status = "held"
        else:
            status = "unaccounted"
        output.append({
            "raw_row_sha256": sha,
            "current_occurrences": len(rows),
            "source_disposition": rows[0]["disposition"],
            "parse_error": "|".join(parse_errors),
            "old_notice_ids": "|".join(map(str, sorted({v["notice_id"] for v in old_versions}))),
            "old_dedupe_keys": "|".join(sorted({v["dedupe_key"] for v in old_versions})),
            "new_notice_id": admitted[0]["id"] if admitted else "",
            "new_dedupe_key": admitted[0]["dedupe_key"] if admitted else "",
            "new_source_identity": admitted[0]["source_identity"] if admitted else "",
            "admission_status": status,
            "exception_reasons": "|".join(row_reasons),
        })
    if any(row["admission_status"] == "unaccounted" for row in output):
        raise ValueError("NJ current raw rows lack admission or an exception")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output_csv = out_dir / "nj_old_to_new_observations.csv"
    with output_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    by_old_id: dict[str, list[dict]] = defaultdict(list)
    for row in output:
        for old_id in filter(None, row["old_notice_ids"].split("|")):
            if row["new_notice_id"]:
                by_old_id[old_id].append(row)
    fanouts = []
    for old_id, rows in sorted(by_old_id.items(), key=lambda item: int(item[0])):
        new_ids = sorted({str(row["new_notice_id"]) for row in rows})
        if len(new_ids) < 2:
            continue
        raw = [raw_by_sha[row["raw_row_sha256"]] for row in rows]
        values = lambda key: "|".join(sorted({str(item.get(key) or "") for item in raw}))
        fanouts.append({
            "old_notice_id": old_id, "new_observations": len(new_ids),
            "new_notice_ids": "|".join(new_ids),
            "raw_row_hashes": "|".join(sorted({row["raw_row_sha256"] for row in rows})),
            "companies": values("Company"), "cities": values("City"),
            "posting_months": values("Month Posted"),
            "effective_source_texts": values("Effective Date"),
            "worker_source_texts": values("Workforce Affected"),
            "review_status": "unresolved_event_identity",
        })
    fanout_csv = out_dir / "nj_old_key_fanout_review.csv"
    with fanout_csv.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FANOUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(fanouts)
    statuses = {name: sum(row["admission_status"] == name for row in output)
                for name in ("admitted", "held", "parse_failure")}
    manifest = {
        "format": "warn-nj-transition-v1",
        "correspondence_csv_sha256": _sha(csv_path),
        "old_candidate_snapshot_sha256": source_manifest["candidate_db_sha256"],
        "new_candidate_snapshot_sha256": new_sha,
        "exception_ledger_sha256": _sha(Path(exceptions_path)),
        "output_csv_sha256": _sha(output_csv),
        "old_key_fanout_csv_sha256": _sha(fanout_csv),
        "old_keys_with_multiple_new_observations": len(fanouts),
        "unique_current_raw_rows": len(output),
        "current_raw_occurrences": len(source_rows),
        "statuses": statuses,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--correspondence-dir", required=True, type=Path)
    parser.add_argument("--new-db", required=True, type=Path)
    parser.add_argument("--exceptions", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.correspondence_dir, args.new_db,
                           args.exceptions, args.out_dir), sort_keys=True))


if __name__ == "__main__":
    main()
