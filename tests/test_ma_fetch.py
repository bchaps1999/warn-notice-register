"""Massachusetts collector: live fetch, block handling, and archived-copy fallback.

Replays the pinned 2026-09-23 mass.gov capture through fake HTTP sessions, so
no test touches the network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from warnlive.fetch.custom import ma

SNAPSHOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/ma/2026-09-23"
BLOCK_PAGE = b"<!DOCTYPE html><html><head><title>Not allowed | Mass Gov</title></head></html>"


def _snapshot_files() -> dict[str, bytes]:
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text())
    return {
        entry["source_url"]: (SNAPSHOT / entry["path"]).read_bytes()
        for entry in manifest["files"]
        if entry["source_url"].startswith("https://")
    }


def _expected_raw_sha256() -> str:
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text())
    return next(e["sha256"] for e in manifest["files"] if e["path"] == "ma.csv")


class _Response:
    def __init__(self, status_code: int, content: bytes = b"", payload=None):
        self.status_code = status_code
        self.content = content
        self._payload = payload

    def json(self):
        return self._payload


class _MassGov:
    """Serves the pinned capture; URLs in ``blocked`` get the Akamai 403 page."""

    def __init__(self, files: dict[str, bytes], blocked=()):
        self.files = files
        self.blocked = set(blocked)
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.calls.append(url)
        if "*" in self.blocked or url in self.blocked:
            return _Response(403, BLOCK_PAGE)
        return _Response(200, self.files[url])


class _Wayback:
    """CDX + id_ raw replay for the same files, with a 403 capture and a revisit."""

    def __init__(self, files: dict[str, bytes], bodies=None):
        self.files = files
        self.bodies = bodies or {}
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None, params=None):
        self.calls.append(url)
        if url == ma.CDX_URL:
            return _Response(200, b"[...]", payload=[
                ["timestamp", "statuscode", "digest"],
                ["20260801000000", "403", "BLOCKDIGEST"],
                ["20260910000000", "200", "GOODDIGEST"],
                ["20260920000000", "-", "GOODDIGEST"],
                ["20260925000000", "403", "BLOCKDIGEST"],
                ["20260926000000", "-", "BLOCKDIGEST"],
            ])
        prefix = "https://web.archive.org/web/20260920000000id_/"
        assert url.startswith(prefix), url
        original = url[len(prefix):]
        return _Response(200, self.bodies.get(original, self.files[original]))


def _run(tmp_path, live, archive=None, allow_archive=False, monkeypatch=None):
    monkeypatch.setattr(ma, "_new_session", lambda: live)
    if allow_archive:
        monkeypatch.setenv(ma.ARCHIVE_FALLBACK_ENV, "1")
    else:
        monkeypatch.delenv(ma.ARCHIVE_FALLBACK_ENV, raising=False)
    if archive is not None:
        original = ma._Fetcher.__init__

        def init(self, session, allow_archive, **kwargs):
            original(self, session, allow_archive, archive_session=archive,
                     backoff=0, **kwargs)

        monkeypatch.setattr(ma._Fetcher, "__init__", init)
    return ma.scrape(tmp_path / "raw", tmp_path / "cache")


def test_live_fetch_reproduces_pinned_raw_and_records_provenance(tmp_path, monkeypatch):
    files = _snapshot_files()
    path = _run(tmp_path, _MassGov(files), monkeypatch=monkeypatch)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == _expected_raw_sha256()
    manifest = json.loads((tmp_path / "raw" / ma.MANIFEST_NAME).read_text())
    assert manifest["archived_files"] == 0
    assert manifest["oldest_confirmed_capture"] is None
    assert manifest["raw_sha256"] == _expected_raw_sha256()
    assert {f["source_url"] for f in manifest["files"]} == set(files)
    assert all(f["origin"] == "live" and f["live_status"] == 200 for f in manifest["files"])
    for entry in manifest["files"]:
        assert entry["sha256"] == hashlib.sha256(files[entry["source_url"]]).hexdigest()


def test_block_without_fallback_raises_and_leaves_no_output(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / ma.MANIFEST_NAME).write_text("{\"stale\": true}\n")
    with pytest.raises(ma.SourceBlocked, match="HTTP 403"):
        _run(tmp_path, _MassGov(_snapshot_files(), blocked={"*"}), monkeypatch=monkeypatch)
    assert not (raw / "ma.csv").exists()
    assert not (raw / ma.MANIFEST_NAME).exists()


def test_block_with_fallback_uses_latest_good_capture(tmp_path, monkeypatch):
    files = _snapshot_files()
    csv_url = next(url for url in files if url.endswith(".csv"))
    live = _MassGov(files, blocked={csv_url})
    archive = _Wayback(files)
    path = _run(tmp_path, live, archive, allow_archive=True, monkeypatch=monkeypatch)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == _expected_raw_sha256()
    manifest = json.loads((tmp_path / "raw" / ma.MANIFEST_NAME).read_text())
    assert manifest["archive_fallback_enabled"] is True
    assert manifest["archived_files"] == 1
    assert manifest["oldest_confirmed_capture"] == "20260920000000"
    archived = [f for f in manifest["files"] if f["origin"] == "internet_archive"]
    assert [f["source_url"] for f in archived] == [csv_url]
    entry = archived[0]
    # A revisit of a 200 capture confirms the content; later 403 captures and
    # their revisits are never used.
    assert entry["capture_timestamp"] == "20260910000000"
    assert entry["confirmed_through"] == "20260920000000"
    assert entry["wayback_digest"] == "GOODDIGEST"
    assert entry["live_status"] == 403
    assert entry["capture_url"].startswith("https://web.archive.org/web/20260920000000id_/")


def test_archived_block_page_is_rejected(tmp_path, monkeypatch):
    files = _snapshot_files()
    workbook_url = next(url for url in files if "/doc/" in url)
    archive = _Wayback(files, bodies={workbook_url: BLOCK_PAGE})
    live = _MassGov(files, blocked={workbook_url})
    with pytest.raises(ma.SourceBlocked, match="is not a xlsx"):
        _run(tmp_path, live, archive, allow_archive=True, monkeypatch=monkeypatch)
    assert not (tmp_path / "raw" / "ma.csv").exists()


@pytest.mark.parametrize("kind", ["index", "xlsx", "csv"])
def test_block_page_is_never_a_valid_payload(kind):
    assert not ma._valid_payload(BLOCK_PAGE, kind)
    assert not ma._valid_payload(b"", kind)


def test_session_disables_http3():
    assert ma._new_session()._disable_http3 is True
