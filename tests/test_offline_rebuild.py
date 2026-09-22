import json

import pytest

from warnlive.migrate.offline_rebuild import (
    _cached_only, _read_policy, _repair_il_from_cache, rebuild,
)


def test_offline_rebuild_requires_explicit_policy(tmp_path):
    with pytest.raises(ValueError, match="no rebuild_policy"):
        _read_policy(tmp_path / "missing.json")
    policy = tmp_path / "rebuild_policy.json"
    policy.write_text(json.dumps({"format": "wrong"}))
    with pytest.raises(ValueError, match="unsupported"):
        _read_policy(policy)


def test_offline_cache_reader_never_fetches(tmp_path):
    source = tmp_path / "cached.pdf"
    assert _cached_only("https://unavailable.example/pdf", source) is None
    source.write_bytes(b"%PDF fixture")
    assert _cached_only("https://unavailable.example/pdf", source) == b"%PDF fixture"


def test_offline_rebuild_refuses_to_replace_existing_database(tmp_path):
    db = tmp_path / "current.sqlite"
    db.write_bytes(b"preserve")
    with pytest.raises(FileExistsError, match="already exists"):
        rebuild(tmp_path / "missing.tar.gz", db, "2026-09-22")
    assert db.read_bytes() == b"preserve"


def test_il_repair_uses_only_cached_reports(tmp_path, monkeypatch):
    from warnlive.cli import il_effective_dates
    from warnlive.enrich import il_effective

    cache = tmp_path / "cache" / "il_reports"
    cache.mkdir(parents=True)
    (cache / "one.PDF").write_bytes(b"cached")
    (cache / "ignore.txt").write_text("not a report")
    parsed = []
    monkeypatch.setattr(il_effective, "parse_report", lambda path: parsed.append(path.name) or [])

    def check_callback(_years, workdir, _db, _dry_run):
        assert workdir == tmp_path
        assert il_effective.collect_records(set(), cache) == []

    monkeypatch.setattr(il_effective_dates, "callback", check_callback)
    report = _repair_il_from_cache(tmp_path / "candidate.sqlite", cache)
    assert parsed == ["one.PDF"]
    assert report["cached_files"] == 1
    assert report["parsed_records"] == 0
    assert report["failed_files"] == []


def test_il_repair_skips_an_optional_missing_cache(tmp_path):
    report = _repair_il_from_cache(
        tmp_path / "candidate.sqlite", tmp_path / "cache" / "il_reports"
    )
    assert report["cached_files"] == 0
    assert report["result"].startswith("skipped:")
