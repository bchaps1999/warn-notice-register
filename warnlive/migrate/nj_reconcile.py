"""Read-only, source-first correspondence for frozen New Jersey CSV rows.

An exact correspondence means identical complete prepared-row JSON in two
artifacts. Database keys are nominations only: they do not establish source
or event identity, especially while NJ notice-date semantics are in flux.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import tarfile
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote

from warnlive.migrate.source_bundle import verify
from warnlive.normalize.engine import get_transformer_class, normalize_file


FIELDS = (
    "artifact", "artifact_sha256", "prepared_row", "raw_row_sha256",
    "artifact_occurrences", "bundle_occurrences", "other_artifact_occurrences",
    "disposition", "raw_json", "parse_error", "normalized_source_id",
    "normalized_dedupe_key", "candidate_source_ids", "candidate_keys",
    "candidate_versions",
)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str,
                      separators=(",", ":"))


def _candidates(db_path: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]], str]:
    uri = quote(Path(db_path).resolve().as_posix(), safe="/:")
    source = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    snapshot_dir = tempfile.TemporaryDirectory(prefix="warn-nj-candidate-snapshot-")
    snapshot_path = Path(snapshot_dir.name) / "candidate.sqlite"
    snapshot = sqlite3.connect(snapshot_path)
    try:
        source.backup(snapshot)
        snapshot.close()
        source.close()
        digest = hashlib.sha256()
        with snapshot_path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        conn = sqlite3.connect(f"file:{quote(snapshot_path.as_posix(), safe='/:')}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        by_id: dict[str, list[dict]] = defaultdict(list)
        by_key: dict[str, list[dict]] = defaultdict(list)
        query = (
            "SELECT n.id,n.source_notice_id,n.dedupe_key,n.current_version,"
            "v.version,v.raw_record_hash FROM notices n "
            "LEFT JOIN notice_versions v ON v.notice_id=n.id "
            "WHERE n.state='NJ' ORDER BY n.id,v.version"
        )
        for notice_id, source_id, key, current, version, record_hash in conn.execute(query):
            item = {"notice_id": notice_id, "source_notice_id": source_id,
                    "dedupe_key": key, "current_version": current,
                    "version": version, "raw_record_hash": record_hash}
            if source_id:
                by_id[source_id].append(item)
            if key:
                by_key[key].append(item)
        return by_id, by_key, digest.hexdigest()
    finally:
        snapshot.close()
        source.close()
        if "conn" in locals():
            conn.close()
        snapshot_dir.cleanup()


def _artifact_rows(content: bytes, artifact: str) -> list[dict]:
    # Use the same transformer preparation and normalization as the rebuild.
    # Each input lives in a temporary directory; the frozen bundle and DB stay read-only.
    with tempfile.TemporaryDirectory(prefix="warn-nj-correspondence-") as temp:
        path = Path(temp) / "nj.csv"
        path.write_bytes(content)
        transformer = get_transformer_class("nj")(Path(temp))
        prepared = transformer.prep_row_list(transformer.raw_data)
        frozen = [_json({k if k is not None else "_restkey": v
                         for k, v in row.items()}) for row in prepared]
        result = normalize_file("nj", Path(temp), None)
    if len(prepared) != result.raw_rows:
        raise ValueError(f"prepared row count changed for {artifact}")
    records = {row["prepared_row"]: row for row in result.records}
    failures = {row["prepared_row"]: row for row in result.failures}
    if len(records) + len(failures) != len(prepared):
        raise ValueError(f"normalization did not account for {artifact} rows")
    artifact_sha = hashlib.sha256(content).hexdigest()
    return [{"artifact": artifact, "artifact_sha256": artifact_sha,
             "prepared_row": ordinal, "raw_row_sha256": hashlib.sha256(raw.encode()).hexdigest(),
             "raw_json": raw, "parse_error": failures.get(ordinal, {}).get("error", ""),
             "normalized_source_id": records.get(ordinal, {}).get("source_notice_id") or "",
             "normalized_dedupe_key": records.get(ordinal, {}).get("dedupe_key") or ""}
            for ordinal, raw in enumerate(frozen, 1)]


def build(bundle: Path, candidate_db: Path, out_dir: Path) -> dict:
    """Verify the bundle and write a complete NJ correspondence CSV and summary."""
    bundle, out_dir = Path(bundle), Path(out_dir)
    manifest = verify(bundle)
    artifacts = [item["path"] for item in manifest["files"]
                 if item["path"] == "raw/nj.csv" or
                 (item["path"].startswith("backfill/raw/") and
                  Path(item["path"]).name == "nj.csv")]
    if not artifacts:
        raise ValueError("bundle contains no NJ raw source artifact")
    rows = []
    with tarfile.open(bundle, "r:gz") as archive:
        for artifact in sorted(artifacts):
            stream = archive.extractfile(artifact)
            if stream is None:
                raise ValueError(f"unreadable artifact: {artifact}")
            rows.extend(_artifact_rows(stream.read(), artifact))
    by_artifact = Counter((r["artifact"], r["raw_row_sha256"]) for r in rows)
    all_occurrences = Counter(r["raw_row_sha256"] for r in rows)
    by_id, by_key, candidate_sha256 = _candidates(candidate_db)
    dispositions = Counter()
    parse_failures = Counter()
    for row in rows:
        sha = row["raw_row_sha256"]
        own = by_artifact[row["artifact"], sha]
        other = all_occurrences[sha] - own
        if other == 0:
            disposition = "unmatched"
        elif own == 1 and other == 1:
            disposition = "exact"
        else:
            disposition = "ambiguous"
        row.update(artifact_occurrences=own, bundle_occurrences=all_occurrences[sha],
                   other_artifact_occurrences=other, disposition=disposition)
        source_id = row["normalized_source_id"]
        key = row["normalized_dedupe_key"]
        id_items = by_id.get(source_id, []) if source_id else []
        key_items = by_key.get(key, []) if key else []
        all_items = {(item["notice_id"], item["version"]): item
                     for item in id_items + key_items}
        row["candidate_source_ids"] = _json(sorted({item["source_notice_id"] for item in id_items
                                                       if item["source_notice_id"]}))
        row["candidate_keys"] = _json(sorted({item["dedupe_key"] for item in key_items
                                               if item["dedupe_key"]}))
        row["candidate_versions"] = _json([all_items[k] for k in sorted(all_items)])
        dispositions[disposition] += 1
        if row["parse_error"]:
            parse_failures[row["artifact"]] += 1
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "nj_source_correspondence.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "format": "warn-nj-source-correspondence-v1",
        "bundle": str(bundle.resolve()),
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "candidate_db": str(Path(candidate_db).resolve()),
        "candidate_db_sha256": candidate_sha256,
        "candidate_hash_basis": "sqlite_backup_snapshot_including_committed_wal",
        "artifacts": {artifact: {"sha256": next(row["artifact_sha256"] for row in rows
                                                   if row["artifact"] == artifact),
                                "prepared_rows": sum(row["artifact"] == artifact for row in rows),
                                "parse_failures": parse_failures[artifact]}
                      for artifact in sorted(artifacts)},
        "dispositions": dict(sorted(dispositions.items())),
        "rows": len(rows),
        "csv": str(csv_path.resolve()),
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
    }
    (out_dir / "nj_source_correspondence.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--candidate-db", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.bundle, args.candidate_db, args.out_dir), indent=2))


if __name__ == "__main__":
    main()
