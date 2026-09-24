"""Only source-backed notice relationships survive a rebuild."""

import pytest

from warnlive.store import db as db_mod
from warnlive.store.links import rebuild


@pytest.fixture()
def conn(tmp_path):
    conn = db_mod.connect(tmp_path / "test.sqlite")
    db_mod.init_db(conn)
    return conn


def insert(conn, name, date):
    cur = conn.execute(
        """INSERT INTO notices (dedupe_key, state, employer_name, location,
           notice_date, source_url, source_notice_id, first_seen, last_seen)
           VALUES (?, 'CT', ?, 'Hartford, CT', ?, 'u', ?, '2026-07-16', '2026-07-16')""",
        (f"{name}:{date}", name, date, f"source:{date}"),
    )
    conn.commit()
    return cur.lastrowid


def link(conn, source, target, method, kind="possible_duplicate"):
    conn.execute(
        """INSERT INTO notice_links
           (notice_id, related_id, kind, score, method, detail, created_at)
           VALUES (?, ?, ?, 0.9, ?, 'test', '2026-07-01')""",
        (source, target, kind, method),
    )
    conn.commit()


def test_name_and_date_similarity_do_not_create_relationships(conn):
    insert(conn, "Acme Corp", "2026-06-01")
    insert(conn, "Acme Corp (Amended)", "2026-06-20")
    assert rebuild(conn) == {"links": 0, "by_kind_method": {}}


def test_rebuild_removes_legacy_inferences_and_keeps_source_links(conn):
    a = insert(conn, "Acme Corp", "2026-06-01")
    b = insert(conn, "Acme Corp (Amended)", "2026-06-20")
    link(conn, b, a, "marker", "amendment_of")
    link(conn, b, a, "date-repair")

    first = rebuild(conn)
    second = rebuild(conn)
    assert first == second == {
        "links": 1, "by_kind_method": {"possible_duplicate/date-repair": 1},
    }
    assert [r["method"] for r in conn.execute("SELECT method FROM notice_links")] == [
        "date-repair"
    ]
