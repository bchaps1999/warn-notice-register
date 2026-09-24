import pytest

from warnlive.migrate.clean_rebuild import (
    _coalesced_observations, _quarantine_date_collisions, _raw_exception, build,
)
from warnlive.normalize.admission import exclusion_reasons


def test_candidate_quarantines_only_differing_idless_ga_groups():
    rows = [
        {"dedupe_key": "no-id", "raw_record_hash": "a", "source_identity": None},
        {"dedupe_key": "no-id", "raw_record_hash": "b", "source_identity": None},
        {"dedupe_key": "id", "raw_record_hash": "a", "source_identity": "GA:1"},
        {"dedupe_key": "id", "raw_record_hash": "b", "source_identity": "GA:1"},
    ]
    assert set(exclusion_reasons("ga", rows)) == {"no-id"}
    assert set(exclusion_reasons("ia", rows)) == {"no-id", "id"}
    assert set(exclusion_reasons("ks", rows)) == {"no-id", "id"}


def test_nj_repeated_source_observation_is_held_even_if_bytes_match():
    rows = [
        {"dedupe_key": "repeat", "raw_record_hash": "same", "source_identity": "NJ:raw-row:1"},
        {"dedupe_key": "repeat", "raw_record_hash": "same", "source_identity": "NJ:raw-row:1"},
        {"dedupe_key": "unique", "raw_record_hash": "other", "source_identity": "NJ:raw-row:2"},
        {"dedupe_key": "missing", "raw_record_hash": "third", "source_identity": None},
    ]
    assert set(exclusion_reasons("nj", rows)) == {"repeat", "missing"}


def test_illinois_requires_id_and_quarantines_conflicting_same_id():
    rows = [
        {"dedupe_key": "missing", "raw_record_hash": "a", "source_identity": None},
        {"dedupe_key": "conflict", "raw_record_hash": "a", "source_identity": "IL:IEBS:1"},
        {"dedupe_key": "conflict", "raw_record_hash": "b", "source_identity": "IL:IEBS:1"},
        {"dedupe_key": "identical", "raw_record_hash": "a", "source_identity": "IL:IEBS:2"},
        {"dedupe_key": "identical", "raw_record_hash": "a", "source_identity": "IL:IEBS:2"},
    ]
    assert exclusion_reasons("il", rows) == {
        "missing": "missing_source_identity", "conflict": "conflicting_same_key",
    }


def test_excluded_parseable_row_keeps_notice_year():
    row = _raw_exception("raw/ia.csv", "conflicting_same_key", {
        "state": "IA", "notice_date": "2024-06-01", "prepared_row": 3,
    })
    assert row["notice_year"] == "2024"


def test_coalesced_raw_row_has_pointer_without_hiding_later_versions():
    rows = [
        {"dedupe_key": "one", "raw_record_hash": "a", "prepared_row": 1,
         "raw_extra": '{"row":1}'},
        {"dedupe_key": "one", "raw_record_hash": "a", "prepared_row": 2,
         "raw_extra": '{"row":2}'},
        {"dedupe_key": "one", "raw_record_hash": "b", "prepared_row": 3,
         "raw_extra": '{"row":3}'},
        {"dedupe_key": "one", "raw_record_hash": "a", "prepared_row": 4,
         "raw_extra": '{"row":4}'},
    ]
    excluded = _coalesced_observations("raw/al.csv", rows)
    assert [(r["prepared_row"], r["survivor_prepared_row"]) for r in excluded] == [(2, 1)]
    assert excluded[0]["reason"] == "coalesced_same_key_content"
    assert excluded[0]["source_row_sha256"] is not None


def test_far_apart_same_key_source_rows_are_held_with_both_pointers(tmp_path):
    from warnlive.store import db

    conn = db.connect(tmp_path / "candidate.sqlite")
    db.init_db(conn)
    rows = [
        {"state": "AL", "dedupe_key": "ambiguous", "raw_record_hash": "a",
         "effective_date": "2008-02-11", "prepared_row": 1, "raw_extra": "{}"},
        {"state": "AL", "dedupe_key": "ambiguous", "raw_record_hash": "b",
         "effective_date": "2009-02-11", "prepared_row": 2, "raw_extra": "{}"},
        {"state": "AL", "dedupe_key": "distinct", "raw_record_hash": "c",
         "effective_date": "2009-02-11", "prepared_row": 3, "raw_extra": "{}"},
    ]
    kept, held, keys = _quarantine_date_collisions(conn, "raw/al.csv", rows)
    assert keys == 1
    assert [row["dedupe_key"] for row in kept] == ["distinct"]
    assert [row["prepared_row"] for row in held] == [1, 2]
    assert {row["reason"] for row in held} == {"suspected_same_key_collision"}
    assert all(row["date_conflicts"] for row in held)


def test_candidate_refuses_to_overwrite_existing_database(tmp_path):
    existing = tmp_path / "current.sqlite"
    existing.write_bytes(b"keep me")
    with pytest.raises(ValueError, match="already exists"):
        build(existing, tmp_path / "raw", tmp_path / "cache")
    assert existing.read_bytes() == b"keep me"


def test_missing_raw_source_is_recorded_as_exception(tmp_path):
    exceptions = []
    report = build(
        tmp_path / "candidate.sqlite", tmp_path / "raw", tmp_path / "cache",
        states={"la"}, observed_at="2026-09-22", exceptions=exceptions,
    )
    assert report["states"]["LA"]["status"] == "missing_raw"
    assert exceptions == [{
        "origin": "raw/la.csv", "state": "LA", "reason": "missing_source_file",
    }]


def test_clean_rebuild_rejects_cached_mixed_history(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "ia.csv").write_text("Company,Notice Date\nHistorical Co,2020-01-01\n")
    with pytest.raises(RuntimeError, match="unresolved BLN history boundary"):
        build(tmp_path / "candidate.sqlite", raw, tmp_path / "cache",
              states={"ia"}, observed_at="2026-09-23", exceptions=[])


def test_candidate_rejects_invalid_observation_date_before_creating_db(tmp_path):
    target = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="observed_at must be"):
        build(target, tmp_path / "raw", tmp_path / "cache", observed_at="2026-02-30")
    assert not target.exists()


def test_source_only_state_ingest_error_cannot_publish_staged_exclusions(
    tmp_path, monkeypatch,
):
    from warnlive.migrate import clean_rebuild
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.verify.harness import VerificationResult

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "al.csv").write_text("placeholder\n")
    rec = {"state": "AL", "dedupe_key": "same", "raw_record_hash": "hash",
           "prepared_row": 1, "raw_extra": '{}'}
    norm = NormalizeResult(state="AL", records=[rec, {**rec, "prepared_row": 2}],
                           raw_rows=2)
    monkeypatch.setattr(clean_rebuild, "normalize_file", lambda *_: norm)
    monkeypatch.setattr(
        clean_rebuild, "verify_state", lambda *_: VerificationResult(state="AL")
    )
    monkeypatch.setattr(
        clean_rebuild, "ingest", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("forced ingest failure")
        )
    )
    exceptions = []

    with pytest.raises(RuntimeError, match="source-only state AL failed"):
        build(tmp_path / "candidate.sqlite", raw, tmp_path / "cache",
              states={"al"}, exceptions=exceptions)

    assert exceptions == []
