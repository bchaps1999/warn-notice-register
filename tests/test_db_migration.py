from warnlive.store import db


def test_v4_database_gets_additive_source_detail_columns(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite")
    conn.execute(
        "CREATE TABLE notices ("
        "id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE, state TEXT, "
        "employer_name TEXT, location TEXT, notice_date TEXT, effective_date TEXT, "
        "employees_affected INTEGER, layoff_type TEXT, is_temporary INTEGER, "
        "is_amendment INTEGER, source_url TEXT, source_notice_id TEXT, "
        "is_amended INTEGER, current_version INTEGER, first_seen TEXT, "
        "last_seen TEXT, site_address TEXT)"
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (4)")
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, first_seen) "
        "VALUES ('old', 'NJ', '2026-01-01')"
    )
    conn.commit()
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 5
    row = conn.execute(
        "SELECT dedupe_key, effective_date_end, notice_date_precision, "
        "notice_date_basis, source_identity, source_details FROM notices"
    ).fetchone()
    assert row["dedupe_key"] == "old"
    assert all(row[key] is None for key in (
        "effective_date_end", "notice_date_precision", "notice_date_basis",
        "source_identity", "source_details",
    ))
