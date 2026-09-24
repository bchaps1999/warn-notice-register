"""Massachusetts receipt-role checks against the pinned FY2020 source workbook."""

from __future__ import annotations

import json
import hashlib
import tarfile
from pathlib import Path

from warnlive.backfill.state_archives import fetch_ma
from warnlive.normalize.engine import _fold


BUNDLE = Path(__file__).resolve().parents[1] / "data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz"


def test_ma_fy2020_workbook_receipt_keeps_old_key(tmp_path):
    with tarfile.open(BUNDLE) as archive:
        source = archive.extractfile("backfill/cache/archives/ma/fy2020.xls")
        assert source is not None
        target = tmp_path / "archives/ma/fy2020.xls"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read())

    rows = fetch_ma(tmp_path)
    assert len(rows) == 173
    assert len({row["dedupe_key"] for row in rows}) == 173
    assert all(row["notice_date"] is None for row in rows)
    for row in rows:
        detail = json.loads(row["source_details"])
        receipt = detail["agency_received_date"]
        assert receipt and detail["dates"][0]["role"] == "agency_received"
        expected = hashlib.sha1(
            f"MA|{_fold(row['employer_name'])}|{receipt}|{_fold(row['location'])}".encode()
        ).hexdigest()
        assert row["dedupe_key"] == expected
