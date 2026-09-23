import pytest

from warnlive.migrate.clean_rebuild import (
    _ga_idless_conflicts, _ia_conflicts, _ks_conflicts, build,
)


def test_candidate_quarantines_only_differing_idless_ga_groups():
    rows = [
        {"dedupe_key": "no-id", "raw_record_hash": "a", "source_identity": None},
        {"dedupe_key": "no-id", "raw_record_hash": "b", "source_identity": None},
        {"dedupe_key": "id", "raw_record_hash": "a", "source_identity": "GA:1"},
        {"dedupe_key": "id", "raw_record_hash": "b", "source_identity": "GA:1"},
    ]
    assert _ga_idless_conflicts(rows) == {"no-id"}
    assert _ia_conflicts(rows) == {"no-id", "id"}
    assert _ks_conflicts(rows) == {"no-id", "id"}


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


def test_candidate_rejects_invalid_observation_date_before_creating_db(tmp_path):
    target = tmp_path / "candidate.sqlite"
    with pytest.raises(ValueError, match="observed_at must be"):
        build(target, tmp_path / "raw", tmp_path / "cache", observed_at="2026-02-30")
    assert not target.exists()
