"""Only source-backed notice relationships survive a rebuild."""

import json

import pytest

from warnlive.store import db as db_mod
from warnlive.store import links as links_mod
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


def _notice(conn, key, state, employer, *, identity=None, workers=None, details=None):
    conn.execute(
        """INSERT INTO notices (dedupe_key, state, employer_name, location,
           employees_affected, layoff_type, source_identity, source_details,
           first_seen) VALUES (?, ?, ?, 'Chicago', ?, 'closure', ?, ?, '2026-09-29')""",
        (key, state, employer, workers, identity,
         json.dumps(details) if details else None),
    )


def test_source_groups_create_additive_sibling_entry_links(conn):
    _notice(conn, "ny-a", "NY", "Rite Aid", details={"filing_group": {"id": "g1", "rows": 2}})
    _notice(conn, "ny-b", "NY", "Rite Aid", details={"filing_group": "g1"})
    _notice(conn, "ny-c", "NY", "Rite Aid", details={"filing_group": "g2"})
    _notice(conn, "il-a", "IL", "ABC Manufacturing", identity="IL:IEBS:1", workers=83)
    _notice(conn, "il-b", "IL", "ABC Manufacturing LLC", identity="IL:IEBS:2", workers=83)
    # No date and no worker count: identical blanks are not evidence.
    _notice(conn, "il-c", "IL", "Army", identity="IL:IEBS:3")
    _notice(conn, "il-d", "IL", "Army", identity="IL:IEBS:4")
    first = rebuild(conn)
    assert first == rebuild(conn)
    assert first["by_kind_method"] == {"sibling_entry/source_group": 2}
    pairs = conn.execute(
        "SELECT a.dedupe_key, b.dedupe_key, l.detail FROM notice_links l "
        "JOIN notices a ON a.id = l.notice_id JOIN notices b ON b.id = l.related_id "
        "ORDER BY 1").fetchall()
    assert [(a, b) for a, b, _ in pairs] == [("il-b", "il-a"), ("ny-b", "ny-a")]
    assert json.loads(pairs[0][2])["basis"] == "identical_content_distinct_source_records"
    assert "source_group" not in links_mod.RETIRED_METHODS
