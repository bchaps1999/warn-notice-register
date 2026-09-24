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
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='source_observations'").fetchone()[0] == "source_observations"
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='cleaning_repairs'").fetchone() is None
    row = conn.execute(
        "SELECT dedupe_key, effective_date_end, notice_date_precision, "
        "notice_date_basis, effective_date_precision, effective_date_basis, "
        "effective_date_end_precision, effective_date_end_basis, "
        "source_identity, source_details FROM notices"
    ).fetchone()
    assert row["dedupe_key"] == "old"
    assert all(row[key] is None for key in (
        "effective_date_end", "notice_date_precision", "notice_date_basis",
        "effective_date_precision", "effective_date_basis",
        "effective_date_end_precision", "effective_date_end_basis",
        "source_identity", "source_details",
    ))


def test_v6_database_gets_nullable_effective_metadata_idempotently(tmp_path):
    conn = db.connect(tmp_path / "v6.sqlite")
    conn.execute(
        "CREATE TABLE notices ("
        "id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE, state TEXT, "
        "employer_name TEXT, location TEXT, notice_date TEXT, effective_date TEXT, "
        "effective_date_end TEXT, notice_date_precision TEXT, notice_date_basis TEXT, "
        "source_identity TEXT, source_details TEXT, employees_affected INTEGER, "
        "layoff_type TEXT, is_temporary INTEGER, is_amendment INTEGER, "
        "source_url TEXT, source_notice_id TEXT, is_amended INTEGER, "
        "current_version INTEGER, first_seen TEXT, last_seen TEXT, site_address TEXT)"
    )
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    conn.execute("INSERT INTO schema_version VALUES (6)")
    conn.execute(
        "INSERT INTO notices (dedupe_key, state, notice_date, effective_date, "
        "effective_date_end, notice_date_precision, first_seen) "
        "VALUES ('existing', 'NY', '2024-03-21', '2024-06-30', '2024-06-30', "
        "'day', '2026-09-23')"
    )
    conn.commit()
    db.init_db(conn)
    db.init_db(conn)
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM source_observations").fetchone()[0] == 0
    row = conn.execute("SELECT * FROM notices WHERE dedupe_key='existing'").fetchone()
    assert (row["notice_date"], row["effective_date"], row["effective_date_end"],
            row["notice_date_precision"]) == (
                "2024-03-21", "2024-06-30", "2024-06-30", "day")
    for field in ("effective_date_precision", "effective_date_basis",
                  "effective_date_end_precision", "effective_date_end_basis"):
        assert row[field] is None
        assert sum(item["name"] == field for item in conn.execute(
            "PRAGMA table_info(notices)")) == 1


def test_existing_legacy_cleaning_data_is_preserved_on_init(tmp_path):
    conn = db.connect(tmp_path / "legacy.sqlite")
    conn.execute("CREATE TABLE cleaning_repairs (id INTEGER PRIMARY KEY, marker TEXT)")
    conn.execute("INSERT INTO cleaning_repairs (marker) VALUES ('historical')")
    conn.execute("""CREATE TABLE source_observations (
        id INTEGER PRIMARY KEY, source_bundle_sha256 TEXT, state TEXT,
        source_artifact TEXT, source_row TEXT, admission_status TEXT,
        input_sha256 TEXT, cleaning_provenance_json TEXT)""")
    conn.execute("""INSERT INTO source_observations
        (source_bundle_sha256, source_artifact, source_row, input_sha256, cleaning_provenance_json) VALUES ('bundle',
         'agency/la/file.pdf', 'r1', 'legacy-hash',
         '{"model":"historical"}')""")
    conn.commit()
    db.init_db(conn)
    assert conn.execute("SELECT marker FROM cleaning_repairs").fetchone()[0] == "historical"
    assert conn.execute(
        "SELECT cleaning_provenance_json FROM source_observations"
    ).fetchone()[0] == '{"model":"historical"}'
