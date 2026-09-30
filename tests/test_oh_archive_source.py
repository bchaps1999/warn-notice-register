"""Ohio 2023-2025 WARN page tables archived on the Wayback Machine."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.oh_archive_source import project, read_artifacts

ARCHIVE = Path(__file__).resolve().parents[1] / "data/source_snapshots/oh/wayback-2026-09-30"


@lru_cache(maxsize=1)
def _projected():
    return project(ARCHIVE, {"011-22-055"})


def test_every_page_row_is_admitted_or_held_once_per_notice_id():
    records, held, report = _projected()
    assert (report["source_rows"], report["distinct_notice_ids"]) == (351, 246)
    assert report["source_rows"] == len(records) + len(held)
    assert report["hold_reasons"] == {
        "already_represented_oh_id": 1, "amendment_without_original_listing": 4,
        "conflicting_original_notice_id": 13, "duplicate_agency_capture": 90,
        "invalid_or_multiple_notice_id": 4, "superseding_amendment_observation": 3,
    }
    assert report["admitted"] == 236
    assert len({rec["source_notice_id"] for rec in records}) == 236
    assert all(rec["notice_date"] is None for rec in records)


def test_received_date_is_agency_receipt_and_ids_match_annual_keys():
    records, _, _ = _projected()
    syncreon = next(rec for rec in records if rec["source_notice_id"] == "009-23-078")
    assert (syncreon["employer_name"], syncreon["effective_date"], syncreon["employees_affected"],
            syncreon["location"]) == ("syncreon America, Inc", "2024-02-05", 68, "Toledo/Lucas")
    details = json.loads(syncreon["source_details"])
    assert details["agency_received_date"] == "2023-12-14"
    assert details["date_roles"]["Date Received"] == "agency_receipt"
    # The same key rule as oh_annual_source: one notice per Notice ID.
    import hashlib
    assert syncreon["dedupe_key"] == hashlib.sha1(b"OH|source|009-23-078").hexdigest()


def test_repeats_phases_and_irregular_ids_are_held():
    _, held, _ = _projected()
    by_id = {}
    for item in held:
        by_id.setdefault(item["source_notice_id"], set()).add(item["reason"])
    assert by_id["011-22-055"] == {"already_represented_oh_id"}
    assert by_id["003-23-025"] == {"conflicting_original_notice_id"}  # four David's Bridal dates
    assert by_id["007-24/052"] == {"invalid_or_multiple_notice_id"}
    assert "superseding_amendment_observation" in by_id["003-24-008"]


def test_current_rows_without_ids_are_not_repeated():
    records, _, _ = _projected()
    again, held, _ = project(ARCHIVE, {"011-22-055"}, {("syncreonamer", "2023-12-14")})
    assert len(again) == len(records) - 1
    assert any(item["reason"] == "possible_overlap_with_current_oh_row" for item in held)


def test_ohio_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    name = manifest["artifacts"][0]["file"]
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_artifacts(tmp_path)
