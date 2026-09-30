"""Official Louisiana table extraction preserves evidence and uncertainties."""

from collections import Counter
from pathlib import Path
import hashlib
import json

import pytest

from warnlive.migrate.la_source import (
    EMPLOYERS, HELD, extract, record, source_exceptions, validate_source_rows,
)


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
        if path.is_file():  # skip dated evidence subdirectories
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


V12_BUNDLE = SOURCE.parent / "2026-09-30-v1.2-source-bundle.tar.gz"


@pytest.fixture(scope="module")
def v12_la(tmp_path_factory):
    """The agency/la tables exactly as the v1.2 release replay reads them."""
    import tarfile

    target = tmp_path_factory.mktemp("v12-la")
    with tarfile.open(V12_BUNDLE, "r:gz") as archive:
        for name in ("manifest.json", "2025.pdf", "2026.pdf"):
            source = archive.extractfile(f"agency/la/{name}")
            assert source is not None
            (target / name).write_bytes(source.read())
    return extract(target), target


def test_reviewed_projection_is_pinned_to_the_release_table(v12_la, tmp_path):
    rows, directory = v12_la
    validate_source_rows(rows, directory)
    (tmp_path / "manifest.json").write_bytes((directory / "manifest.json").read_bytes() + b"\n")
    with pytest.raises(ValueError, match="manifest drift"):
        validate_source_rows(rows, tmp_path)
    with pytest.raises(ValueError, match="source rows drift"):
        validate_source_rows(rows[:-1], directory)
    with pytest.raises(ValueError, match="not accepted"):
        record(next(row for row in rows if row["source_row"] in HELD))


def test_official_projection_preserves_uncertain_dates_and_addresses(v12_la):
    rows, _ = v12_la
    records = {row["source_row"]: record(row) for row in rows
               if row["kind"] == "notice" and row["source_row"] in EMPLOYERS}
    assert len(records) == len({r["dedupe_key"] for r in records.values()}) == 31
    assert records["2025.pdf:p1:r10"]["effective_date_end"] == "2025-12-31"
    assert records["2025.pdf:p2:r16"]["notice_date"] is None
    assert {records[f"2026.pdf:p1:r{n}"]["employees_affected"]
            for n in (13, 14, 15)} == {172, 206, 216}
    assert all(rec["location"] is None for rec in records.values())
    assert all(rec["source_identity"] == f"LA:official:{key}" for key, rec in records.items())
    mosaic = json.loads(records["2026.pdf:p1:r14"]["source_details"])
    assert mosaic["worker_allocation"] == "unresolved"
    assert len(mosaic["sites"]) == 2
    assert all(site["workers"] is None for site in mosaic["sites"])
    assert Counter(item["reason"] for item in source_exceptions(rows)) == {
        "official_source_held_for_review": 6,
        "official_source_rescinded": 1,
        "official_source_annotation": 1,
    }
