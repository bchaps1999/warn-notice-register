import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from warnlive.migrate.source_bundle import create, extract, validate_agency_raw, verify


def _sources(root):
    for relative, content in {
        "raw/al.csv": b"Company\nAcme\n",
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
    assert len(manifest["files"]) == 3
    assert manifest["admission_inputs"] == "agency-only-v1"
    assert all("bln" not in item["path"] and not item["path"].startswith("backfill/raw/")
               for item in manifest["files"])
    destination = tmp_path / "unpacked"
    assert extract(first, destination) == manifest
    assert (destination / "raw/al.csv").read_bytes() == b"Company\nAcme\n"
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
    (source / "raw/al.csv").unlink()
    (source / "raw").rmdir()
    with pytest.raises(FileNotFoundError):
        create(source, tmp_path / "missing.tar.gz")


def test_source_bundle_rejects_old_database_overlap_policy(tmp_path):
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
    with pytest.raises(ValueError, match="overlap policy is retired"):
        create(source, destination, db_path)
    assert not destination.exists()


def test_agency_raw_guard_rejects_embedded_historical_mirrors(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "tx.csv").write_text("NOTICE_DATE,JOB_SITE_NAME\n2020-01-01,Agency Co\n")
    (raw / "oh.csv").write_text("Company,URL\nAgency Co,https://ohio.gov/notice\n")
    validate_agency_raw(raw)
    (raw / "tx.csv").write_text(
        "NOTICE_DATE,JOB_SITE_NAME\n2020-01-01,Agency Co\n2019-01-01,Mirror Co\n"
    )
    with pytest.raises(ValueError, match="Texas raw capture"):
        validate_agency_raw(raw)
    (raw / "tx.csv").unlink()
    (raw / "oh.csv").write_text(
        "Company,URL\nAgency Co,https://ohio.gov/notice\nMirror Co,\n"
    )
    with pytest.raises(ValueError, match="Ohio raw capture"):
        validate_agency_raw(raw)
    (raw / "oh.csv").unlink()
    (raw / "ia.csv").write_text("Company\nMixed source\n")
    with pytest.raises(ValueError, match="IA raw capture"):
        validate_agency_raw(raw)


def test_bundle_creation_rejects_mixed_state_raw(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    (source / "raw/tn.csv").write_text("Company\nBLN history\n")
    with pytest.raises(ValueError, match="pinned agency-only projection"):
        create(source, tmp_path / "mixed.tar.gz")
    assert not (tmp_path / "mixed.tar.gz").exists()


def test_source_bundle_can_layer_fresh_raw_without_changing_base(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    overlay = tmp_path / "fresh"
    overlay.mkdir()
    (overlay / "al.csv").write_bytes(b"Company\nUpdated\n")
    archive = tmp_path / "fresh.tar.gz"
    manifest = create(source, archive, raw_overlay=overlay)
    assert manifest["raw_overrides"] == ["raw/al.csv"]
    unpacked = tmp_path / "unpacked"
    extract(archive, unpacked)
    assert (unpacked / "raw/al.csv").read_bytes() == b"Company\nUpdated\n"
    assert (source / "raw/al.csv").read_bytes() == b"Company\nAcme\n"


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


def test_source_bundle_includes_verified_new_york_dashboard(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    ny = Path(__file__).resolve().parents[1] / "data/source_snapshots/ny"
    archive = tmp_path / "with-ny.tar.gz"
    manifest = create(source, archive, ny_artifacts=ny)
    assert {"agency/ny/manifest.json", "agency/ny/ny_warn_2022.csv",
            "agency/ny/ny_warn_2023.csv", "agency/ny/ny_warn_2024.csv",
            "agency/ny/filings/3e-logistics-2024-0066.pdf",
            "agency/ny/filings/first-transit-2023-0298.pdf",
            "agency/ny/reviewed_date_repairs.json"} <= {
        item["path"] for item in manifest["files"]
    }
    unpacked = tmp_path / "unpacked"
    extract(archive, unpacked)
    assert (unpacked / "agency/ny/ny_warn_2024.csv").read_bytes() == (
        ny / "ny_warn_2024.csv"
    ).read_bytes()
    assert (unpacked / "agency/ny/filings/3e-logistics-2024-0066.pdf").read_bytes() == (
        ny / "filings/3e-logistics-2024-0066.pdf"
    ).read_bytes()
    assert (unpacked / "agency/ny/filings/first-transit-2023-0298.pdf").read_bytes() == (
        ny / "filings/first-transit-2023-0298.pdf"
    ).read_bytes()
