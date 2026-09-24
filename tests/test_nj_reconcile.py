"""Source correspondence must be based on full rows, not date-derived keys."""

import csv
import hashlib
import io
import json
import sqlite3
import tarfile

from warnlive.migrate import nj_reconcile


def _bundle(path, files):
    manifest = {"format": "warn-source-bundle-v1", "files": [
        {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in files.items()]}
    with tarfile.open(path, "w:gz") as archive:
        for name, data in {"manifest.json": json.dumps(manifest).encode(), **files}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_exact_ambiguous_unmatched_and_candidate_nominations(tmp_path, monkeypatch):
    bundle = tmp_path / "source.tar.gz"
    files = {"raw/nj.csv": b"raw", "backfill/raw/nj.csv": b"historical"}
    _bundle(bundle, files)
    db = tmp_path / "candidate.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE notices(id INTEGER, state TEXT, source_notice_id TEXT,
            dedupe_key TEXT, current_version INTEGER);
        CREATE TABLE notice_versions(notice_id INTEGER, version INTEGER,
            raw_record_hash TEXT);
        INSERT INTO notices VALUES(7,'NJ','id-a','key-a',2);
        INSERT INTO notice_versions VALUES(7,1,'old');
        INSERT INTO notice_versions VALUES(7,2,'new');
    """)
    conn.close()

    def fake_rows(content, artifact):
        names = ["a", "b", "b", "c"] if artifact == "raw/nj.csv" else ["a", "b", "d"]
        return [{"artifact": artifact, "artifact_sha256": hashlib.sha256(content).hexdigest(),
                 "prepared_row": i, "raw_row_sha256": hashlib.sha256(name.encode()).hexdigest(),
                 "raw_json": json.dumps({"row": name}),
                 "parse_error": "bad date" if name == "c" else "",
                 "normalized_source_id": "id-a" if name == "a" else "",
                 "normalized_dedupe_key": "key-a" if name == "d" else ""}
                for i, name in enumerate(names, 1)]

    monkeypatch.setattr(nj_reconcile, "_artifact_rows", fake_rows)
    summary = nj_reconcile.build(bundle, db, tmp_path / "report")
    with (tmp_path / "report/nj_source_correspondence.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert summary["dispositions"] == {"ambiguous": 3, "exact": 2, "unmatched": 2}
    assert summary["artifacts"]["raw/nj.csv"]["parse_failures"] == 1
    assert rows[0]["disposition"] == "exact"
    assert rows[1]["disposition"] == "ambiguous"
    assert next(row for row in rows if row["artifact"] == "raw/nj.csv" and
                row["prepared_row"] == "2")["artifact_occurrences"] == "2"
    assert next(row for row in rows if row["artifact"] == "raw/nj.csv" and
                row["prepared_row"] == "4")["parse_error"] == "bad date"
    assert json.loads(rows[0]["candidate_source_ids"]) == ["id-a"]
    assert [v["version"] for v in json.loads(rows[0]["candidate_versions"])] == [1, 2]
    key_row = next(row for row in rows if row["artifact"] == "backfill/raw/nj.csv" and
                   row["prepared_row"] == "3")
    assert json.loads(key_row["candidate_keys"]) == ["key-a"]
    assert key_row["disposition"] == "unmatched"
