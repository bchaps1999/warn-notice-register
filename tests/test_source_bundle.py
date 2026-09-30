import hashlib
import json
from pathlib import Path

import pytest

from warnlive.migrate.source_bundle import (
    add_agency, add_archives, create, extract, overlay_raw_bundle, validate_agency_raw, verify,
)


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


def test_frozen_bundle_overlay_changes_only_named_raw_source(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    base = tmp_path / "base.tar.gz"
    original = create(source, base)
    replacement = tmp_path / "al.csv"
    replacement.write_bytes(b"Company\nNew agency row\n")
    evidence = tmp_path / "agency-pages.tar.gz"
    evidence.write_bytes(b"frozen source evidence")
    out = tmp_path / "updated.tar.gz"
    manifest = overlay_raw_bundle(base, replacement, out, evidence)
    assert manifest["raw_overrides"] == ["raw/al.csv"]
    assert manifest["companion_evidence"]["name"] == evidence.name
    assert manifest["companion_evidence"]["raw_member"] == "raw/al.csv"
    before = {item["path"]: item["sha256"] for item in original["files"]}
    after = {item["path"]: item["sha256"] for item in manifest["files"]}
    assert {name for name in before if before[name] != after[name]} == {"raw/al.csv"}
    assert verify(out) == manifest
    with pytest.raises(FileExistsError):
        overlay_raw_bundle(base, replacement, out)


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


def _members(bundle):
    import tarfile

    with tarfile.open(bundle, "r:gz") as archive:
        return {m.name: archive.extractfile(m).read() for m in archive.getmembers()}


def test_add_archives_copies_base_and_adds_only_new_archive_files(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    base = tmp_path / "base.tar.gz"
    original = create(source, base)
    additions = tmp_path / "additions"
    for relative, content in {"archives/ne/warn_report-2014.html": b"<html>2014</html>",
                              "archives/ne/warn_report-2014.html.json": b"{}\n",
                              "archives/wi/dwd-2016.htm": b"<html>wi</html>"}.items():
        path = additions / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    out = tmp_path / "added.tar.gz"
    manifest = add_archives(base, additions, out)
    added = {"backfill/cache/archives/ne/warn_report-2014.html",
             "backfill/cache/archives/ne/warn_report-2014.html.json",
             "backfill/cache/archives/wi/dwd-2016.htm"}
    assert manifest["archive_additions"] == sorted(added)
    assert verify(out) == manifest
    before, after = _members(base), _members(out)
    assert set(after) - set(before) == added
    # Every base member is copied byte for byte; only the manifest changes.
    assert all(after[name] == content for name, content in before.items() if name != "manifest.json")
    assert after["backfill/cache/archives/wi/dwd-2016.htm"] == b"<html>wi</html>"
    assert [item["path"] for item in manifest["files"]] == sorted(
        [item["path"] for item in original["files"]] + sorted(added))
    # Deterministic: the same inputs give the same bytes.
    again = tmp_path / "again.tar.gz"
    add_archives(base, additions, again)
    assert again.read_bytes() == out.read_bytes()
    with pytest.raises(FileExistsError):
        add_archives(base, additions, out)
    # Additions accumulate in a later derivation and never replace a member.
    more = tmp_path / "more"
    (more / "archives/ne").mkdir(parents=True)
    (more / "archives/ne/warn-page-20260930.html").write_bytes(b"page")
    later = add_archives(out, more, tmp_path / "later.tar.gz")
    assert len(later["archive_additions"]) == 4
    with pytest.raises(ValueError, match="never replace"):
        add_archives(out, additions, tmp_path / "replace.tar.gz")
    assert not (tmp_path / "replace.tar.gz").exists()


def test_add_archives_rejects_bad_inputs(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    base = tmp_path / "base.tar.gz"
    create(source, base)
    wrong = tmp_path / "wrong"
    (wrong / "raw").mkdir(parents=True)
    (wrong / "raw/al.csv").write_bytes(b"Company\n")
    with pytest.raises(ValueError, match="not an archive cache file"):
        add_archives(base, wrong, tmp_path / "a.tar.gz")
    empty = tmp_path / "empty"
    (empty / "archives").mkdir(parents=True)
    with pytest.raises(ValueError, match="no archive files"):
        add_archives(base, empty, tmp_path / "b.tar.gz")
    linked = tmp_path / "linked"
    (linked / "archives/ne").mkdir(parents=True)
    (linked / "archives/ne/page.html").symlink_to(source / "raw/al.csv")
    with pytest.raises(ValueError, match="symlink"):
        add_archives(base, linked, tmp_path / "c.tar.gz")
    corrupt = tmp_path / "corrupt.tar.gz"
    corrupt.write_bytes(base.read_bytes()[:-40])
    with pytest.raises(Exception):
        add_archives(corrupt, tmp_path / "additions-none", tmp_path / "d.tar.gz")
    assert not any((tmp_path / name).exists() for name in ("a.tar.gz", "b.tar.gz", "c.tar.gz", "d.tar.gz"))


def test_add_archives_command_line(tmp_path):
    import subprocess
    import sys

    source = tmp_path / "workdir"
    _sources(source)
    base = tmp_path / "base.tar.gz"
    create(source, base)
    additions = tmp_path / "additions"
    (additions / "archives/ne").mkdir(parents=True)
    (additions / "archives/ne/page.html").write_bytes(b"page")
    out = tmp_path / "out.tar.gz"
    result = subprocess.run(
        [sys.executable, "-m", "warnlive.migrate.source_bundle", "add-archives", str(base),
         "--archives", str(additions), "--out", str(out)],
        capture_output=True, text=True, check=True, cwd=Path(__file__).resolve().parents[1])
    assert json.loads(result.stdout)["files"] == 4
    assert verify(out)["archive_additions"] == ["backfill/cache/archives/ne/page.html"]


def test_add_agency_adds_a_new_pinned_directory_only(tmp_path):
    source = tmp_path / "workdir"
    _sources(source)
    base = tmp_path / "base.tar.gz"
    create(source, base)
    artifacts = tmp_path / "ky-archive"
    artifacts.mkdir()
    (artifacts / "manifest.json").write_bytes(b'{"format": "fixture"}\n')
    (artifacts / "report.csv").write_bytes(b"Company\nAcme\n")
    out = tmp_path / "out.tar.gz"
    manifest = add_agency(base, artifacts, "ky_archive", out)
    assert manifest["agency_additions"] == ["agency/ky_archive"]
    before, after = _members(base), _members(out)
    assert set(after) - set(before) == {"agency/ky_archive/manifest.json",
                                        "agency/ky_archive/report.csv"}
    assert all(after[name] == content for name, content in before.items() if name != "manifest.json")
    again = tmp_path / "again.tar.gz"
    add_agency(base, artifacts, "ky_archive", again)
    assert again.read_bytes() == out.read_bytes()
    with pytest.raises(ValueError, match="never replace"):
        add_agency(out, artifacts, "ky_archive", tmp_path / "twice.tar.gz")
    with pytest.raises(ValueError, match="invalid agency artifact name"):
        add_agency(base, artifacts, "../ky", tmp_path / "bad-name.tar.gz")
    (artifacts / "manifest.json").unlink()
    with pytest.raises(ValueError, match="lacks manifest.json"):
        add_agency(base, artifacts, "tn_archive", tmp_path / "no-manifest.tar.gz")
    assert not any((tmp_path / name).exists() for name in
                   ("twice.tar.gz", "bad-name.tar.gz", "no-manifest.tar.gz"))
