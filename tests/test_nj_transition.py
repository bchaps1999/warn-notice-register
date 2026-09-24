"""Old NJ keys remain comparison pointers while source rows get dispositions."""

import csv
import hashlib
import json
import sqlite3

import pytest

from warnlive.migrate.nj_transition import build


def test_transition_accounts_for_admitted_held_and_failed_rows(tmp_path):
    prior = tmp_path / "prior"
    prior.mkdir()
    rows = [
        ("a" * 64, 1, "exact", "", "[]"),
        ("b" * 64, 2, "ambiguous", "", "[]"),
        ("b" * 64, 3, "ambiguous", "", "[]"),
        ("c" * 64, 4, "unmatched", "bad date", "[]"),
    ]
    csv_path = prior / "nj_source_correspondence.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "artifact", "raw_row_sha256", "prepared_row", "disposition",
            "parse_error", "candidate_versions",
        ])
        writer.writeheader()
        for sha, ordinal, disposition, error, versions in rows:
            writer.writerow({"artifact": "raw/nj.csv", "raw_row_sha256": sha,
                             "prepared_row": ordinal, "disposition": disposition,
                             "parse_error": error, "candidate_versions": versions})
    (prior / "nj_source_correspondence.json").write_text(json.dumps({
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "candidate_db_sha256": "old-snapshot",
    }))
    db = tmp_path / "new.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notices (id INTEGER, state TEXT, dedupe_key TEXT, "
                 "source_identity TEXT, source_details TEXT)")
    conn.execute("INSERT INTO notices VALUES (1,'NJ','key-a',?,?)",
                 ("NJ:raw-row:" + "a" * 64, json.dumps({"source_row_sha256": "a" * 64})))
    conn.commit()
    conn.close()
    exceptions = tmp_path / "exceptions.jsonl"
    exceptions.write_text("".join(json.dumps({
        "origin": "raw/nj.csv", "prepared_row": n, "reason": reason,
    }) + "\n" for n, reason in ((2, "duplicate_source_observation"),
                               (3, "duplicate_source_observation"),
                               (4, "parse_failure"))))
    result = build(prior, db, exceptions, tmp_path / "out")
    assert result["statuses"] == {"admitted": 1, "held": 1, "parse_failure": 1}
    assert result["current_raw_occurrences"] == 4
    with (tmp_path / "out/nj_old_to_new_observations.csv").open(newline="") as fh:
        output = list(csv.DictReader(fh))
    assert [row["admission_status"] for row in output] == [
        "admitted", "held", "parse_failure",
    ]
    assert output[1]["current_occurrences"] == "2"

    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO notices VALUES (2,'NJ','unmapped','NJ:raw-row:x',?)",
                 (json.dumps({"source_row_sha256": "x" * 64}),))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="absent from current raw source"):
        build(prior, db, exceptions, tmp_path / "bad")


def test_transition_requires_current_exception_for_historical_parse_error(tmp_path):
    prior = tmp_path / "prior"
    prior.mkdir()
    csv_path = prior / "nj_source_correspondence.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "artifact", "raw_row_sha256", "prepared_row", "disposition",
            "parse_error", "candidate_versions",
        ])
        writer.writeheader()
        writer.writerow({"artifact": "raw/nj.csv", "raw_row_sha256": "a" * 64,
                         "prepared_row": 1, "disposition": "unmatched",
                         "parse_error": "old parser failed", "candidate_versions": "[]"})
    (prior / "nj_source_correspondence.json").write_text(json.dumps({
        "csv_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "candidate_db_sha256": "old-snapshot",
    }))
    db = tmp_path / "new.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE notices (id INTEGER, state TEXT, dedupe_key TEXT, "
                 "source_identity TEXT, source_details TEXT)")
    conn.close()
    exceptions = tmp_path / "exceptions.jsonl"
    exceptions.write_text("")
    with pytest.raises(ValueError, match="lack admission or an exception"):
        build(prior, db, exceptions, tmp_path / "out")

    exceptions.write_text(json.dumps({"origin": "raw/nj.csv", "prepared_row": 1,
                                      "reason": "parse_failure"}) + "\n")
    assert build(prior, db, exceptions, tmp_path / "with-exception")["statuses"] == {
        "admitted": 0, "held": 0, "parse_failure": 1,
    }
