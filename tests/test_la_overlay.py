"""Frozen Louisiana source-first decisions and BLN accounting."""

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from warnlive.migrate.la_overlay import correspondence, record, source_exceptions
from warnlive.migrate.la_source import extract as extract_la
from warnlive.migrate.source_bundle import extract as extract_bundle
from warnlive.migrate.offline_rebuild import _bln_unresolved
from warnlive.store import db
from warnlive.store.dedupe import ingest


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "data/source_snapshots/2026-09-23-la-official.tar.gz"


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    target = tmp_path_factory.mktemp("la-frozen") / "sources"
    extract_bundle(BUNDLE, target)
    la = target / "agency/la"
    bln = target / "backfill/bln_integrated.csv"
    rows = extract_la(la)
    return rows, la, bln


def test_reviewed_correspondence_is_pinned_and_accounts_for_held_rows(frozen, tmp_path):
    rows, la, bln = frozen
    mapping = correspondence(rows, la, bln)
    assert len(mapping) == 58
    assert Counter((item["disposition"], item["is_superseded"])
                   for item in mapping.values()) == {
                       ("accepted", False): 44, ("accepted", True): 1,
                       ("held", False): 9, ("rescinded", False): 4,
                   }
    assert Counter(item["match_basis"] for item in mapping.values()) == {
        "reviewed_date_workers": 56, "reviewed_swapped_dates": 2,
    }
    with bln.open(newline="") as fh:
        blue_cross = next(row for row in csv.DictReader(fh)
                          if row["company"] == "Blue Cross Blue Shield of LA")
    assert blue_cross["hash_id"] not in mapping  # A similar-era row is not blanket-suppressed.

    conn = db.connect(tmp_path / "empty.sqlite")
    try:
        db.init_db(conn)
        exceptions = []
        report = _bln_unresolved(conn, bln, exceptions, mapping)
    finally:
        conn.close()
    assert report["official_overlay_excluded_rows"] == 57
    assert report["unaccounted_rows"] == 0
    assert Counter(item["reason"] for item in exceptions)["held_with_official_source"] == 9
    assert Counter(item["reason"] for item in exceptions)["rescinded_by_official_source"] == 4


def test_official_projection_preserves_uncertain_dates_and_addresses(frozen):
    rows, _, _ = frozen
    records = {row["source_row"]: record(row) for row in rows
               if row["kind"] == "notice" and row["source_row"] not in {
                   "2025.pdf:p1:r7", "2025.pdf:p1:r8", "2025.pdf:p2:r5",
                   "2025.pdf:p2:r7", "2025.pdf:p2:r11", "2025.pdf:p2:r12",
                   "2025.pdf:p2:r13",
               }}
    assert len(records) == len({r["dedupe_key"] for r in records.values()}) == 31
    assert records["2025.pdf:p1:r10"]["effective_date_end"] == "2025-12-31"
    assert records["2025.pdf:p2:r16"]["notice_date"] is None
    assert {records[f"2026.pdf:p1:r{n}"]["employees_affected"]
            for n in (13, 14, 15)} == {172, 206, 216}
    assert all(rec["location"] is None for rec in records.values())
    mosaic = json.loads(records["2026.pdf:p1:r14"]["source_details"])
    assert mosaic["worker_allocation"] == "unresolved"
    assert len(mosaic["sites"]) == 2
    assert all(site["workers"] is None for site in mosaic["sites"])
    assert Counter(item["reason"] for item in source_exceptions(rows)) == {
        "official_source_held_for_review": 6,
        "official_source_rescinded": 1,
        "official_source_annotation": 1,
    }


def test_reviewed_correspondence_rejects_source_drift(frozen, tmp_path):
    rows, la, bln = frozen
    altered = tmp_path / "altered.csv"
    altered.write_bytes(bln.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="BLN input drift"):
        correspondence(rows, la, altered)


def test_overlay_ingestion_keeps_unrelated_bln_and_official_date_facts(
    frozen, tmp_path, monkeypatch,
):
    from warnlive.migrate import offline_rebuild

    rows, la, bln = frozen
    mapping = correspondence(rows, la, bln)
    by_id = {row["source_row"]: row for row in rows}
    conn = db.connect(tmp_path / "overlay.sqlite")
    try:
        db.init_db(conn)
        ingest(conn, [record(by_id["2025.pdf:p1:r10"]),
                      record(by_id["2025.pdf:p2:r16"])], "2026-09-22")
        assert conn.execute(
            "SELECT effective_date_end FROM notices WHERE source_notice_id = ?",
            ("2025.pdf:p1:r10",),
        ).fetchone()[0] == "2025-12-31"
        assert conn.execute(
            "SELECT notice_date FROM notices WHERE source_notice_id = ?",
            ("2025.pdf:p2:r16",),
        ).fetchone()[0] is None

        captured = []
        held_id = next(ident for ident, item in mapping.items()
                       if item["disposition"] == "held")
        rescinded_id = next(ident for ident, item in mapping.items()
                            if item["disposition"] == "rescinded")
        historical_id = "unrelated-historical-la-row"

        def select(*_args, **_kwargs):
            return {"LA": [{"source_notice_id": ident} for ident in
                           (held_id, rescinded_id, historical_id)]}

        def collect(_conn, groups, _observed_at):
            captured.extend(rec["source_notice_id"] for group in groups.values()
                            for rec in group)
            return {"new": len(captured), "updated": 0, "unchanged": 0,
                    "coalesced": 0, "suspected_collisions": 0}

        monkeypatch.setattr(offline_rebuild.bln_integrated, "older_rows_by_state", select)
        monkeypatch.setattr(offline_rebuild.bln_integrated, "gap_rows_by_state", select)
        monkeypatch.setattr(offline_rebuild, "_ingest_groups", collect)
        report = offline_rebuild._bln_conservative(conn, bln, "2026-09-22", mapping)
        assert captured == [historical_id, historical_id]
        assert report["older"]["official_overlay_excluded_rows"] == 2
        assert report["empty_months"]["official_overlay_excluded_rows"] == 2
    finally:
        conn.close()
