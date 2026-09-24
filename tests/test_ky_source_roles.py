"""Kentucky source receipt and effective-date roles against the frozen table."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from warnlive.normalize.engine import normalize_file


BUNDLE = Path(__file__).resolve().parents[1] / "data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz"


def test_frozen_ky_received_dates_are_not_legal_notice(tmp_path):
    with tarfile.open(BUNDLE) as archive:
        source = archive.extractfile("raw/ky.csv")
        assert source is not None
        (tmp_path / "ky.csv").write_bytes(source.read())
    result = normalize_file("ky", tmp_path, "https://kcc.ky.gov/warn")
    assert result.raw_rows == len(result.records) == 828
    assert result.failed_rows == 0
    assert len({row["dedupe_key"] for row in result.records}) == 806
    assert all(row["notice_date"] is None for row in result.records)
    details = [json.loads(row["source_details"]) for row in result.records]
    assert sum(detail["agency_received_date"] is not None for detail in details) == 819
    assert all(detail["date_evidence_rule"] == "ky_agency_received_role_v1"
               for detail in details)
    assert not any("received_date_status" in detail for detail in details)
