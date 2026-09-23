"""Official Louisiana table extraction preserves evidence and uncertainties."""

from pathlib import Path

import pytest

from warnlive.migrate.la_source import extract


SOURCE = Path(__file__).resolve().parents[1] / "data/source_snapshots/la"


def test_la_source_rows_preserve_dates_status_and_location_roles():
    rows = extract(SOURCE)
    assert len(rows) == 39
    assert sum(row["kind"] == "notice" for row in rows) == 38
    assert len({row["source_row"] for row in rows}) == 39
    assert all(row["source_url"].startswith("https://www.laworks.net/") for row in rows)

    cornerstone = next(row for row in rows if row["source_row"] == "2025.pdf:p1:r10")
    assert (cornerstone["notice_date"], cornerstone["effective_date_start"],
            cornerstone["effective_date_end"]) == (
                "2025-05-05", "2025-07-31", "2025-12-31")
    assert cornerstone["date_interpretation"] == "interval"
    assert cornerstone["address_text"] is None  # combined 2025 cell

    ups = next(row for row in rows if row["source_row"] == "2025.pdf:p2:r5")
    note = next(row for row in rows if row["kind"] == "annotation")
    assert ups["status"] == "rescinded"
    assert ups["status_source_row"] == note["source_row"]
    assert note["applies_to_source_row"] == ups["source_row"]

    westlake = next(row for row in rows if "Westlake" in row.get("company_text", ""))
    assert westlake["notice_date"] == "2025-12-15"
    assert westlake["report_year"] == 2026
    mosaic = next(row for row in rows if "Mosaic" in row.get("company_text", ""))
    assert "Convent" in mosaic["address_text"]
    assert "St. James" in mosaic["address_text"]
    assert mosaic["workers_total"] == 206
    assert mosaic["worker_allocation"] == "unverified"
    assert mosaic["address_role"] == "unverified"


def test_la_source_rejects_tampered_pdf(tmp_path):
    for path in SOURCE.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "2026.pdf").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        extract(tmp_path)
