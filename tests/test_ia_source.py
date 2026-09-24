"""Official Iowa observations and conservative event decisions."""

from collections import Counter, defaultdict
from pathlib import Path

import pytest

from warnlive.migrate.ia_source import extract, extract_historical, project


SOURCE = Path(__file__).resolve().parents[1] / "data/source_snapshots/ia"


def test_iowa_source_accounts_for_rows_and_preserves_amendment_evidence():
    rows = extract(SOURCE)
    assert len(rows) == 573
    assert len({row["source_row"] for row in rows}) == 573
    assert Counter(row["notice_date"][:4] if row["notice_date"] else "invalid"
                   for row in rows) == {
        "2021": 17, "2022": 98, "2023": 92,
        "2024": 122, "2025": 155, "2026": 89,
    }
    assert all(row["address_role"] == "unverified" for row in rows)
    assert all(row["decision_status"] != "admitted" for row in rows)
    assert {row["address_state_text"].strip() for row in rows} == {
        "IA", "CA", "VA", "SD",
    }
    # Identical cells retain separate source-row pointers until reviewed.
    duplicates = defaultdict(list)
    for row in rows:
        duplicates[row["source_row_sha256"]].append(row["source_row"])
    assert sorted(group for group in duplicates.values() if len(group) > 1) == [[
        "event-log.xlsx:WARN Log:r477", "event-log.xlsx:WARN Log:r478",
    ]]
    cnh = [row for row in rows if row["company_text"].strip() == "CNH Industrial America LLC"
           and row["notice_date"] == "2026-02-27"]
    assert len(cnh) == 14
    assert len({row["effective_date"] for row in cnh}) == 14
    assert all("Change in Number" in row["notice_type_text"] for row in cnh)


def test_iowa_source_rejects_tampered_workbook(tmp_path):
    for path in SOURCE.iterdir():
        (tmp_path / path.name).write_bytes(path.read_bytes())
    (tmp_path / "event-log.xlsx").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        extract(tmp_path)


def test_iowa_historical_pdf_accounts_for_rows_and_exposes_layout_loss():
    rows = extract_historical(SOURCE)
    assert len(rows) == 362
    assert len({row["source_row"] for row in rows}) == 362
    assert Counter(row["notice_date"][:4] if row["notice_date"] else "invalid"
                   for row in rows) == {
        "2018": 39, "2019": 79, "2020": 63,
        "2021": 35, "2022": 98, "2023": 47,
        "invalid": 1,
    }
    issues = {row["source_row"]: row["layout_issues"]
              for row in rows if row["layout_issues"]}
    assert issues == {
        "historical-2023.pdf:p3:r57": ["invalid_notice_date"],
    }
    repaired = {row["source_row"]: row for row in rows if row["company_text"] in {
        "HDS LTD", "Sabre Plumbing",
    }}
    assert repaired["historical-2023.pdf:p5:r52"]["raw_cells"][:4] == [
        "HDS LTD", "8805 Chambers Blvd Ste 300-266", "Johnston", "Polk",
    ]
    assert repaired["historical-2023.pdf:p5:r52"]["source_row_sha256"] == (
        "beaa4688fad643272f86bc6c97898b58d6e3d4a7641589c222bc5270516950e3"
    )
    assert repaired["historical-2023.pdf:p6:r22"]["raw_cells"][:4] == [
        "Sabre Plumbing", "808 South SW Cherry St Suite 111", "Ankeny", "Polk",
    ]
    assert repaired["historical-2023.pdf:p6:r22"]["source_row_sha256"] == (
        "df3be2a687e912253f2e3ba02e13a715270c5e87a53594c06fedb623b1a8e3a2"
    )
    sorenson = next(row for row in rows if row["notice_date_text"] == "9/1/8/2020")
    assert sorenson["company_text"] == "Sorenson Communications, LLC"
    assert sorenson["notice_date"] is None
    tur_pak = next(row for row in rows if row["workers_reported"] == 121
                   and row["notice_date"] == "2022-03-11")
    assert tur_pak["company_text"].startswith("Tur-Pak Foods, Inc Kustom Pak Foods")
    assert tur_pak["company_text"].endswith("United Holding Company, LLC")
    assert rows[0]["raw_cells"][:4] == [
        "Salon Luce, LC", "400 N. Main Street", "Davenport", "Scott",
    ]
    assert rows[-1]["address_state_text"] == "CA"


def test_iowa_projection_accounts_for_rows_and_keeps_uncertain_revisions_out():
    rows = extract(SOURCE) + extract_historical(SOURCE)
    admitted, held, report, related = project(rows)
    assert report["source_rows"] == 935
    assert (len(admitted), len(held)) == (399, 536)
    assert len({item["source_row"] for item in held}) == len(held)
    assert len({item["source_identity"] for item in admitted}) == len(admitted)
    assert report["hold_reasons"]["amendment_without_verified_parent"] == 263
    assert report["hold_reasons"]["duplicate_agency_capture"] == 61
    assert all(item["source_notice_id"] not in related for item in admitted)
    assert all(item["related_source_row"] in {rec["source_notice_id"] for rec in admitted}
               for item in held if item["reason"] == "duplicate_agency_capture")
    assert {item["origin"] for item in held} == {
        "agency/ia/event-log.xlsx", "agency/ia/historical-2023.pdf",
    }


def test_iowa_projection_does_not_depend_on_source_iteration_order():
    rows = extract(SOURCE) + extract_historical(SOURCE)
    admitted, held, report, related = project(rows)
    reversed_admitted, reversed_held, reversed_report, reversed_related = project(
        list(reversed(rows)))
    assert report == reversed_report
    assert {row["source_notice_id"] for row in admitted} == {
        row["source_notice_id"] for row in reversed_admitted}
    assert {(row["source_row"], row["reason"]) for row in held} == {
        (row["source_row"], row["reason"]) for row in reversed_held}
    assert related == reversed_related
