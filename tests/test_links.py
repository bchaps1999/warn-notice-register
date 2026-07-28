import pytest

from warnlive.store import db as db_mod
from warnlive.store.links import detect, rebuild


@pytest.fixture()
def conn(tmp_path):
    conn = db_mod.connect(tmp_path / "test.sqlite")
    db_mod.init_db(conn)
    return conn


def insert(conn, **kw):
    base = dict(
        state="CT", employer_name=None, location="Hartford, CT",
        notice_date="2026-06-01", effective_date="2026-08-01",
        employees_affected=100, layoff_type="closure", is_temporary=None,
        is_amendment=0, source_url="u", source_notice_id="s",
    )
    base.update(kw)
    cur = conn.execute(
        """INSERT INTO notices (dedupe_key, state, employer_name, location, notice_date,
           effective_date, employees_affected, layoff_type, is_temporary, is_amendment,
           source_url, source_notice_id, first_seen, last_seen)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?, '2026-07-16','2026-07-16')""",
        (
            f"key-{base['employer_name']}-{base['notice_date']}-{base['location']}",
            base["state"], base["employer_name"], base["location"],
            base["notice_date"], base["effective_date"], base["employees_affected"],
            base["layoff_type"], base["is_temporary"], base["is_amendment"],
            base["source_url"], base["source_notice_id"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def kinds(links):
    return [(l.kind, l.method) for l in links]


def test_marker_links_to_base(conn):
    a = insert(conn, employer_name="Acme Corp")
    b = insert(conn, employer_name="Acme Corp (Amended)", notice_date="2026-06-20",
               employees_affected=150)
    links, _ = detect(conn)
    assert kinds(links) == [("amendment_of", "marker")]
    assert (links[0].notice_id, links[0].related_id) == (b, a)


def test_declared_amendment_links(conn):
    a = insert(conn, employer_name="Beta LLC")
    insert(conn, employer_name="Beta LLC", notice_date="2026-06-15",
           location="Hartford, CT", is_amendment=1, employees_affected=80)
    links, _ = detect(conn)
    assert ("amendment_of", "declared") in kinds(links)
    assert links[0].related_id == a


def test_refiling_same_location_links(conn):
    insert(conn, employer_name="Gamma Inc")
    insert(conn, employer_name="Gamma Inc", notice_date="2026-06-20")
    links, _ = detect(conn)
    assert kinds(links) == [("amendment_of", "amendment")]


def test_refiling_different_location_is_low_confidence(conn):
    insert(conn, employer_name="Delta Co", location="Hartford, CT")
    insert(conn, employer_name="Delta Co", location="New Haven, CT",
           notice_date="2026-06-20")
    links, _ = detect(conn)
    assert all(k == "possible_duplicate" for k, _ in kinds(links))


def test_digit_guard_blocks_store_numbers(conn):
    insert(conn, employer_name="KMART Store 3528")
    insert(conn, employer_name="KMART Store 3356")
    links, review = detect(conn)
    assert links == [] and review == []


def test_fuzzy_spelling_variant(conn):
    insert(conn, employer_name="Hostess Brands, Inc.")
    insert(conn, employer_name="Hostess Brand, Inc.")
    links, _ = detect(conn)
    assert ("possible_duplicate", "fuzzy") in kinds(links)


def test_rebuild_is_idempotent(conn):
    insert(conn, employer_name="Acme Corp")
    insert(conn, employer_name="Acme Corp (Amended)", notice_date="2026-06-20")
    s1 = rebuild(conn)
    s2 = rebuild(conn)
    assert s1["links"] == s2["links"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM notice_links").fetchone()["c"] == 1


def test_rebuild_preserves_links_it_cannot_reproduce(conn):
    """repair-dates and clean-text record key collisions as links; a dupes
    run must not destroy the only record that those rows are duplicates."""
    a = insert(conn, employer_name="Acme Corp")
    b = insert(conn, employer_name="Acme Corp (Amended)", notice_date="2026-06-20")
    conn.execute(
        "INSERT INTO notice_links (notice_id, related_id, kind, score, method, detail, created_at) "
        "VALUES (?, ?, 'possible_duplicate', 0.9, 'date-repair', 'key collision', '2026-07-01')",
        (b, a),
    )
    conn.commit()
    rebuild(conn)
    kept = conn.execute(
        "SELECT method FROM notice_links ORDER BY method"
    ).fetchall()
    assert [r["method"] for r in kept] == ["date-repair", "marker"]
