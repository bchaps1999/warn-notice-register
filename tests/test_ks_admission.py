import json

import pytest

from warnlive.normalize.admission import (
    ks_ambiguity_reasons, ks_event_signature, load_ks_hold_policy,
    persist_ks_hold_policy,
)


def _record(identity, key, employer="Penske", date="2002-06-25"):
    return {"source_identity": identity, "dedupe_key": key,
            "employer_name": employer, "notice_date": date}


def test_later_kansas_row_is_held_against_stored_notice():
    existing = [_record("KS:315", "old")]
    incoming = [_record("KS:316", "new")]
    assert ks_ambiguity_reasons(incoming, existing, set()) == {
        "new": "same_employer_day_event_identity_unresolved"
    }
    assert ks_ambiguity_reasons([existing[0]], existing, set()) == {}


def test_kansas_batch_holds_all_same_day_rows_and_persisted_ids():
    incoming = [_record("KS:315", "a"), _record("KS:316", "b"),
                _record("KS:400", "c", "Acme", "2026-01-01")]
    assert ks_ambiguity_reasons(incoming, [], {"KS:400"}) == {
        "a": "same_employer_day_event_identity_unresolved",
        "b": "same_employer_day_event_identity_unresolved",
        "c": "reviewed_portal_event_identity_unresolved",
    }
    assert ks_event_signature("Penske, Inc.", "2002-06-25") == (
        "penskeinc", "2002-06-25"
    )
    assert ks_ambiguity_reasons([_record("KS:401", "d", "Acme", "2026-01-01")],
                                [], set(), {("acme", "2026-01-01")}) == {
        "d": "reviewed_portal_event_identity_unresolved"
    }


def test_kansas_hold_policy_requires_unique_source_ids(tmp_path):
    path = tmp_path / "holds.json"
    path.write_text(json.dumps({"source": "kansasworks_warn_portal",
                                "held_ids": ["KS:315", "KS:315"],
                                "held_signatures": []}))
    with pytest.raises(ValueError, match="held IDs"):
        load_ks_hold_policy(path)
    path.write_text(json.dumps({"source": "kansasworks_warn_portal",
                                "held_ids": ["KS:315"],
                                "held_signatures": [["penske", "2002-06-25"]]}))
    assert load_ks_hold_policy(path) == ({"KS:315"}, {("penske", "2002-06-25")})


def test_kansas_pair_hold_survives_later_singleton_capture(tmp_path):
    current = tmp_path / "current.json"
    durable = tmp_path / "durable.json"
    current.write_text(json.dumps({
        "source": "kansasworks_warn_portal",
        "held_ids": ["KS:999001", "KS:999002"],
        "held_signatures": [["acme", "2026-01-01"]],
        "evidence_archive_sha256": "archive-one",
    }))
    persist_ks_hold_policy(current, durable)
    current.write_text(json.dumps({
        "source": "kansasworks_warn_portal", "held_ids": [],
        "held_signatures": [], "evidence_archive_sha256": "archive-two",
    }))
    ids, signatures = load_ks_hold_policy(durable)
    assert {"KS:999001", "KS:999002"} <= ids
    assert ks_ambiguity_reasons(
        [_record("KS:999001", "later", "Acme", "2026-01-01")],
        [], ids, signatures,
    ) == {"later": "reviewed_portal_event_identity_unresolved"}
