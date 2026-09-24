"""Rhode Island date roles and continuous ranges from the frozen agency table."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

from warnlive.normalize.engine import normalize_file


BUNDLE = Path(__file__).resolve().parents[1] / "data/source_snapshots/2026-09-23-ia-la-ny-tx-annual-reviewed.tar.gz"


def test_ri_ranges_do_not_turn_phase_lists_into_continuous_ends(tmp_path):
    with tarfile.open(BUNDLE) as archive:
        source = archive.extractfile("raw/ri.csv")
        assert source is not None
        (tmp_path / "ri.csv").write_bytes(source.read())

    result = normalize_file("ri", tmp_path, "https://dlt.ri.gov/employers/worker-adjustment-and-retraining-notification-warn")
    assert result.raw_rows == 126
    assert result.failed_rows == 1  # Existing source parser failure is unchanged.
    assert len({row["dedupe_key"] for row in result.records}) == 125

    by_name = {row["employer_name"]: row for row in result.records}
    nmc = by_name["NMC"]
    assert (nmc["notice_date"], nmc["effective_date"], nmc["effective_date_end"]) == (
        "2024-05-02", "2024-05-02", "2024-07-01",
    )
    assert json.loads(nmc["source_details"])["effective_date_interpretation"] == "interval"

    phases = by_name["CVS Health"]
    assert phases["effective_date"] == "2023-10-21"
    assert phases.get("effective_date_end") is None
    assert json.loads(phases["source_details"])["effective_date_interpretation"] == "list_or_phases"

    assert sum(bool(row.get("effective_date_end")) for row in result.records) == 5
