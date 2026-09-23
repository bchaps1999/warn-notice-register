import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from warnlive.migrate.source_bundle import create, extract, verify


def _sources(root):
    for relative, content in {
        "raw/ia.csv": b"Company\nAcme\n",
        "backfill/raw/ny.csv": b"Company\nOld Co\n",
        "backfill/bln_integrated.csv": b"postal_code,company\nIA,Acme\n",
        "backfill/cache/archives/ca/2000.pdf": b"%PDF fixture",
        "cache/sc/2026.pdf": b"%PDF second fixture",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def test_source_bundle_is_deterministic_and_checks_every_file(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    first, second = tmp_path / "first.tar.gz", tmp_path / "second.tar.gz"
    manifest = create(source, first)
    create(source, second)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    assert verify(first) == manifest
    assert len(manifest["files"]) == 5
    destination = tmp_path / "unpacked"
    assert extract(first, destination) == manifest
    assert (destination / "raw/ia.csv").read_bytes() == b"Company\nAcme\n"
    with pytest.raises(FileExistsError):
        extract(first, destination)


def test_source_bundle_refuses_overwrite_and_missing_required_input(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    dest = tmp_path / "snapshot.tar.gz"
    create(source, dest)
    before = dest.read_bytes()
    with pytest.raises(FileExistsError):
        create(source, dest)
    assert dest.read_bytes() == before
    (source / "backfill/bln_integrated.csv").unlink()
    with pytest.raises(FileNotFoundError):
        create(source, tmp_path / "missing.tar.gz")


def test_source_bundle_can_freeze_overlap_policy_without_notice_payloads(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    db_path = tmp_path / "baseline.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE notices (dedupe_key TEXT, state TEXT, source_url TEXT, employees_affected INTEGER)")
    conn.execute("INSERT INTO notices VALUES ('one', 'CA', 'https://archive/one', 12)")
    conn.execute("INSERT INTO notices VALUES ('two', 'KS', 'https://example/two', NULL)")
    conn.commit()
    conn.close()
    destination = tmp_path / "snapshot.tar.gz"
    create(source, destination, db_path)
    unpacked = tmp_path / "unpacked"
    extract(destination, unpacked)
    policy = json.loads((unpacked / "rebuild_policy.json").read_text())
    assert policy["accepted_keys"] == ["one", "two"]
    assert policy["archive_source_urls"] == {"one": "https://archive/one"}
    assert policy["baseline_states"]["CA"] == {"notices": 1, "workers": 12}


def test_source_bundle_can_layer_fresh_raw_without_changing_base(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    overlay = tmp_path / "fresh"
    overlay.mkdir()
    (overlay / "ia.csv").write_bytes(b"Company\nUpdated\n")
    archive = tmp_path / "fresh.tar.gz"
    manifest = create(source, archive, raw_overlay=overlay)
    assert manifest["raw_overrides"] == ["raw/ia.csv"]
    unpacked = tmp_path / "unpacked"
    extract(archive, unpacked)
    assert (unpacked / "raw/ia.csv").read_bytes() == b"Company\nUpdated\n"
    assert (source / "raw/ia.csv").read_bytes() == b"Company\nAcme\n"


def test_source_bundle_rejects_unexpected_raw_overlay(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    overlay = tmp_path / "fresh"
    overlay.mkdir()
    (overlay / "notes.txt").write_text("not raw data")
    with pytest.raises(ValueError, match="unexpected file"):
        create(source, tmp_path / "bad.tar.gz", raw_overlay=overlay)


def test_source_bundle_includes_verified_louisiana_documents(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    la = Path(__file__).resolve().parents[1] / "data/source_snapshots/la"
    archive = tmp_path / "with-la.tar.gz"
    manifest = create(source, archive, agency_artifacts=la)
    assert {"agency/la/manifest.json", "agency/la/2025.pdf", "agency/la/2026.pdf"} <= {
        item["path"] for item in manifest["files"]
    }
    unpacked = tmp_path / "unpacked"
    extract(archive, unpacked)
    assert (unpacked / "agency/la/2025.pdf").read_bytes() == (la / "2025.pdf").read_bytes()


def test_source_bundle_includes_verified_iowa_event_log(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    ia = Path(__file__).resolve().parents[1] / "data/source_snapshots/ia"
    archive = tmp_path / "with-ia.tar.gz"
    manifest = create(source, archive, ia_artifacts=ia)
    assert {"agency/ia/manifest.json", "agency/ia/event-log.xlsx",
            "agency/ia/historical-2023.pdf"} <= {
        item["path"] for item in manifest["files"]
    }
    unpacked = tmp_path / "unpacked"
    extract(archive, unpacked)
    assert (unpacked / "agency/ia/event-log.xlsx").read_bytes() == (
        ia / "event-log.xlsx"
    ).read_bytes()
