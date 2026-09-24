"""Official Louisiana table extraction preserves evidence and uncertainties."""

from pathlib import Path
import hashlib
import json

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
    assert ups["rescission_date"] == note["rescission_date"] == "2025-09-05"
    assert ups["rescission_target_source_row"] == note["rescission_target_source_row"] == ups["source_row"]
    assert ups["rescission_source_quote"] == note["rescission_source_quote"] == note["annotation_text"]
    assert note["kind"] == "annotation" and "workers_total" not in note
    assert "notice_date" not in note and "effective_date_end" not in note
    assert ups["effective_date_end"] is None
    assert ups["effective_date_start"] != ups["rescission_date"]

    gdit = next(row for row in rows if row["source_row"] == "2025.pdf:p2:r16")
    assert gdit["notice_date"] is None
    assert gdit["notice_date_text"] == "Not\nspecified"
    assert gdit["effective_date_start"] == "2025-11-14"
    safesource = [row for row in rows if row["source_row"] in {
        "2025.pdf:p2:r7", "2025.pdf:p2:r11", "2025.pdf:p2:r13",
    }]
    assert {row["workers_total"] for row in safesource} == {541, 87, 454}

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


def test_historical_relationship_diagnostic_is_source_bound_and_unresolved():
    path = SOURCE.parents[1] / "archive/source-diagnostics/la-relationships-2026-09-23.json"
    artifact = json.loads(path.read_text())
    rows = {row["source_row"]: row for row in extract(SOURCE)}
    assert artifact["source_manifest_sha256"] == hashlib.sha256(
        (SOURCE / "manifest.json").read_bytes()).hexdigest()
    assert artifact["source_pdf_sha256"] == hashlib.sha256(
        (SOURCE / "2025.pdf").read_bytes()).hexdigest()
    groups = {group["group"]: group for group in artifact["candidate_groups"]}
    assert set(groups) == {"IDEA", "SafeSource"}
    assert {row["source_row"] for row in groups["SafeSource"]["observations"]} == {
        "2025.pdf:p2:r7", "2025.pdf:p2:r11", "2025.pdf:p2:r12",
        "2025.pdf:p2:r13",
    }
    for group in groups.values():
        assert group["relationship_decision"] == "unresolved"
        assert group["publication_action"] == "held_for_review"
        for observation in group["observations"]:
            source_row = rows[observation["source_row"]]
            assert observation["source_row_sha256"] == source_row["source_row_sha256"]
            assert observation["source_pdf_sha256"] == source_row["source_pdf_sha256"]
            assert observation["workers_total"] == source_row["workers_total"]
    assert any(evidence["type"] == "arithmetic_equality"
               for evidence in groups["SafeSource"]["evidence"])
