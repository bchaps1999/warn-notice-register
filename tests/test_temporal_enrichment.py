"""Dated evidence grants only relationships that its source actually covers."""

import hashlib
import json

import pytest

from warnlive.enrich.temporal import EvidenceError, Ledger


def ledger_file(tmp_path, *, identities=None, relationships=None):
    artifact = tmp_path / "source.txt"
    artifact.write_text("official record pinned for this fixture\n")
    payload = {
        "schema_version": 1,
        "sources": [{
            "source_id": "filing", "url": "https://example.gov/filing",
            "local_path": "source.txt",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "retrieved_at": "2026-09-23T00:00:00Z", "published_date": "2020-04-01",
        }],
        "entities": [
            {"entity_id": "child", "name": "Child Inc.", "identifiers": {"cik": "1"}},
            {"entity_id": "parent-a", "name": "Parent A", "identifiers": {}},
            {"entity_id": "parent-b", "name": "Parent B", "identifiers": {}},
        ],
        "identities": identities if identities is not None else [{
            "assertion_id": "identity-1", "source_notice_id": "notice-1",
            "state": "CA", "filed_name": "Child Inc.",
            "entity_id": "child", "source_id": "filing",
        }],
        "relationships": relationships if relationships is not None else [],
    }
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps(payload))
    return path


def relation(assertion_id, parent, kind, **dates):
    return {
        "assertion_id": assertion_id,
        "child_entity_id": "child", "parent_entity_id": parent,
        "relationship_type": "direct_parent", "source_id": "filing",
        "temporal_scope": {"kind": kind, **dates},
    }


def test_snapshot_does_not_become_an_open_ended_parent(tmp_path):
    ledger = Ledger(ledger_file(tmp_path, relationships=[
        relation("merger-close", "parent-a", "snapshot", as_of="2020-04-01")
    ]))
    assert ledger.relationship("child", "direct_parent", "2020-03-31").status == "unknown"
    on_day = ledger.relationship("child", "direct_parent", "2020-04-01")
    assert on_day.status == "accepted" and on_day.parent_entity_id == "parent-a"
    assert ledger.relationship("child", "direct_parent", "2020-04-02").status == "unknown"
    assert ledger.relationship("child", "ultimate_parent", "2020-04-01").status == "unknown"


def test_interval_is_inclusive_and_conflicts_do_not_select_a_parent(tmp_path):
    ledger = Ledger(ledger_file(tmp_path, relationships=[
        relation("interval-a", "parent-a", "interval", **{"from": "2020-01-01", "through": "2020-12-31"}),
        relation("interval-b", "parent-b", "interval", **{"from": "2020-06-01", "through": "2020-06-30"}),
    ]))
    assert ledger.relationship("child", "direct_parent", "2019-12-31").status == "unknown"
    assert ledger.relationship("child", "direct_parent", "2020-01-01").parent_entity_id == "parent-a"
    conflict = ledger.relationship("child", "direct_parent", "2020-06-15")
    assert conflict.status == "conflict" and conflict.parent_entity_id is None
    assert conflict.assertion_ids == ("interval-a", "interval-b")
    assert ledger.relationship("child", "direct_parent", "2020-12-31").parent_entity_id == "parent-a"
    assert ledger.relationship("child", "direct_parent", "2021-01-01").status == "unknown"


def test_identity_is_notice_scoped_and_conflicts_are_explicit(tmp_path):
    path = ledger_file(tmp_path)
    ledger = Ledger(path)
    assert ledger.identity("notice-1", "CA", "Child Inc.").entity_id == "child"
    assert ledger.identity("notice-2", "CA", "Child Inc.").status == "identity_unresolved"
    assert ledger.identity("notice-1", "NY", "Child Inc.").status == "identity_unresolved"
    data = json.loads(path.read_text())
    data["identities"].append({**data["identities"][0], "assertion_id": "identity-2", "entity_id": "parent-a"})
    path.write_text(json.dumps(data))
    conflict = Ledger(path).identity("notice-1", "CA", "Child Inc.")
    assert conflict.status == "conflict" and conflict.entity_id is None


def test_invalid_dates_and_source_tampering_fail_closed(tmp_path):
    path = ledger_file(tmp_path, relationships=[
        relation("bad", "parent-a", "interval", **{"from": "2020-04-02", "through": "2020-04-01"})
    ])
    with pytest.raises(EvidenceError, match="ends before"):
        Ledger(path)
    data = json.loads(path.read_text())
    data["relationships"] = [relation("good", "parent-a", "snapshot", as_of="2020-04-01")]
    path.write_text(json.dumps(data))
    (tmp_path / "source.txt").write_text("changed")
    with pytest.raises(EvidenceError, match="SHA-256 mismatch"):
        Ledger(path)


def test_announced_transaction_is_not_accepted_as_completed_ownership(tmp_path):
    proposed = relation("proposal", "parent-a", "snapshot", as_of="2020-04-01")
    proposed["assertion_status"] = "announced"
    with pytest.raises(EvidenceError, match="not an observed/completed fact"):
        Ledger(ledger_file(tmp_path, relationships=[proposed]))


def test_local_candidate_excerpt_can_use_origin_instead_of_url(tmp_path):
    path = ledger_file(tmp_path)
    data = json.loads(path.read_text())
    data["sources"][0].pop("url")
    data["sources"][0]["origin"] = "frozen candidate SQLite extract"
    path.write_text(json.dumps(data))
    assert Ledger(path).identity("notice-1", "CA", "Child Inc.").status == "accepted"
