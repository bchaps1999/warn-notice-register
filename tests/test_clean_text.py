from click.testing import CliRunner

from warnlive import cli
from warnlive.normalize.engine import _record_hash
from warnlive.store import db
from warnlive.store.dedupe import ingest


def test_clean_text_repairs_existing_canonical_version_mismatch(tmp_path, monkeypatch):
    path = tmp_path / "test.sqlite"
    conn = db.connect(path)
    db.init_db(conn)
    rec = {
        "state": "CT", "employer_name": "Acme Corp", "location": "Hartford",
        "notice_date": "2026-01-01", "effective_date": "2026-03-01",
        "employees_affected": 20, "layoff_type": "closure",
        "is_temporary": None, "is_amendment": 0,
        "source_url": "https://example.gov", "source_notice_id": "source-1",
        "raw_extra": "{}", "dedupe_key": "key-1",
    }
    rec["raw_record_hash"] = _record_hash(rec)
    ingest(conn, [rec], "2026-07-01")
    # Simulate the old clean-text bug: canonical changed without a version.
    conn.execute("UPDATE notices SET employer_name = 'Acme Corporation'")
    conn.commit()
    monkeypatch.setattr(cli, "_compress_db", lambda *_: None)

    runner = CliRunner()
    first = runner.invoke(cli.clean_text, ["--db", str(path)])
    assert first.exit_code == 0, first.output
    row = conn.execute("SELECT current_version, employer_name FROM notices").fetchone()
    assert (row["current_version"], row["employer_name"]) == (2, "Acme Corporation")
    second = runner.invoke(cli.clean_text, ["--db", str(path)])
    assert second.exit_code == 0, second.output
    assert conn.execute("SELECT COUNT(*) FROM notice_versions").fetchone()[0] == 2
