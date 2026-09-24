"""A failed database restore must stop ingestion and preserve the prior DB."""

from __future__ import annotations

import gzip
import sqlite3

from click.testing import CliRunner

from warnlive.cli import cli
from warnlive.store import db


def _write_dump(source, dump):
    with sqlite3.connect(source) as conn, gzip.open(dump, "wt") as fh:
        for line in conn.iterdump():
            fh.write(f"{line}\n")


def _existing_db(path):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('keep')")


def test_unpack_db_validates_before_replacing_existing_database(tmp_path):
    source = tmp_path / "source.sqlite"
    with db.connect(source) as conn:
        db.init_db(conn)
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, first_seen) "
            "VALUES ('one', 'RI', '2026-09-23')"
        )
        conn.commit()
    dump = tmp_path / "warn.sql.gz"
    _write_dump(source, dump)
    target = tmp_path / "warn.sqlite"
    _existing_db(target)

    result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

    assert result.exit_code == 0, result.output
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT dedupe_key FROM notices").fetchone()[0] == "one"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not list(tmp_path.glob(".warn.sqlite.*.tmp"))


def test_unpack_db_rejects_wrong_schema_without_replacing_existing_database(tmp_path):
    source = tmp_path / "wrong.sqlite"
    _existing_db(source)
    _write_dump(source, tmp_path / "warn.sql.gz")
    target = tmp_path / "warn.sqlite"
    _existing_db(target)

    result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

    assert result.exit_code != 0
    assert "missing_tables" in result.output
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "keep"
    assert not list(tmp_path.glob(".warn.sqlite.*.tmp"))


def test_unpack_db_rejects_corrupt_dump_without_replacing_existing_database(tmp_path):
    (tmp_path / "warn.sql.gz").write_bytes(b"not a gzip dump")
    target = tmp_path / "warn.sqlite"
    _existing_db(target)

    result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

    assert result.exit_code != 0
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "keep"
    assert not list(tmp_path.glob(".warn.sqlite.*.tmp"))


def test_unpack_db_rejects_broken_foreign_keys(tmp_path):
    source = tmp_path / "broken.sqlite"
    with db.connect(source) as conn:
        db.init_db(conn)
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, first_seen) "
            "VALUES ('one', 'RI', '2026-09-23')"
        )
        conn.commit()
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO notice_versions "
            "(notice_id, version, raw_record_hash, fields_json, observed_at) "
            "VALUES (999, 1, 'broken', '{}', '2026-09-23')"
        )
        conn.commit()
    _write_dump(source, tmp_path / "warn.sql.gz")
    target = tmp_path / "warn.sqlite"
    _existing_db(target)

    result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

    assert result.exit_code != 0
    assert "foreign_key_errors=1" in result.output
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone()[0] == "keep"


def test_unpack_db_accepts_legacy_database_for_later_migration(tmp_path):
    source = tmp_path / "legacy.sqlite"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version VALUES (4)")
        conn.execute(
            "CREATE TABLE notices ("
            "id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE, state TEXT, "
            "employer_name TEXT, location TEXT, notice_date TEXT, effective_date TEXT, "
            "employees_affected INTEGER, layoff_type TEXT, is_temporary INTEGER, "
            "is_amendment INTEGER, source_url TEXT, source_notice_id TEXT, "
            "is_amended INTEGER, current_version INTEGER, first_seen TEXT, "
            "last_seen TEXT, site_address TEXT)"
        )
        conn.execute("INSERT INTO notices (dedupe_key, state) VALUES ('legacy', 'RI')")
    with open(source, "rb") as src, gzip.open(tmp_path / "warn.sqlite.gz", "wb") as dst:
        dst.write(src.read())
    target = tmp_path / "warn.sqlite"

    result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

    assert result.exit_code == 0, result.output
    with db.connect(target) as conn:
        db.init_db(conn)
        assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT dedupe_key FROM notices").fetchone()[0] == "legacy"


def test_unpack_db_rejects_active_wal_database(tmp_path):
    source = tmp_path / "source.sqlite"
    with db.connect(source) as conn:
        db.init_db(conn)
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, first_seen) "
            "VALUES ('new', 'RI', '2026-09-23')"
        )
        conn.commit()
    _write_dump(source, tmp_path / "warn.sql.gz")
    target = tmp_path / "warn.sqlite"
    conn = db.connect(target)
    try:
        db.init_db(conn)
        conn.execute(
            "INSERT INTO notices (dedupe_key, state, first_seen) "
            "VALUES ('old', 'RI', '2026-09-23')"
        )
        conn.commit()
        assert (tmp_path / "warn.sqlite-wal").exists()

        result = CliRunner().invoke(cli, ["unpack-db", "--db", str(target)])

        assert result.exit_code != 0
        assert "SQLite sidecars exist" in result.output
        assert conn.execute("SELECT dedupe_key FROM notices").fetchone()[0] == "old"
    finally:
        conn.close()
