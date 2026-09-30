"""Official Iowa observations and conservative event decisions."""

import json
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
    assert (len(admitted), len(held)) == (625, 310)
    assert (report["admitted"], report["amendment_versions"]) == (560, 65)
    assert report["admitted_filing_group_rows"] == 161
    assert len({item["source_row"] for item in held}) == len(held)
    notices = [item for item in admitted if not item["is_amendment"]]
    assert len({item["source_identity"] for item in notices}) == len(notices)
    assert report["hold_reasons"]["amendment_without_verified_parent"] == 110
    assert report["hold_reasons"]["amendment_parent_ambiguous"] == 63
    # Declared amendments with one parent site are versions even when they
    # move the action date; their second captures are duplicates.
    assert "amendment_action_date_outside_revision_window" not in report["hold_reasons"]
    assert report["hold_reasons"]["site_phase_or_worker_allocation_unresolved"] == 3
    assert report["hold_reasons"]["duplicate_agency_capture"] == 97
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


def test_iowa_notice_type_and_listed_city_county_are_projected():
    rows = extract(SOURCE) + extract_historical(SOURCE)
    admitted, _, _, _ = project(rows)
    admitted = [rec for rec in admitted if not rec["is_amendment"]]
    types = {}
    for rec in admitted:
        text = " ".join(json.loads(rec["source_details"])["notice_type_text"].split())
        types.setdefault(text, set()).add(rec["layoff_type"])
    assert types["Closing"] == {"closure"}
    assert types["Mass Layoff"] == {"mass_layoff"}
    assert types["Hyvee"] == {"unknown"}
    salon = next(rec for rec in admitted if rec["employer_name"] == "Salon Luce, LC")
    assert salon["location"] == "Davenport, Scott County"
    # A listed address outside Iowa names no Iowa place.
    for rec in admitted:
        details = json.loads(rec["source_details"])
        if (details["address_state_text"] or "").strip() not in ("", "IA"):
            assert rec["location"] is None
    assert sum(rec["location"] is not None for rec in admitted) == 557


def test_iowa_distinct_address_sites_are_entries_of_one_filing_group():
    rows = extract(SOURCE) + extract_historical(SOURCE)
    admitted, held, _, related = project(rows)
    groups = defaultdict(list)
    for rec in admitted:
        group = json.loads(rec["source_details"]).get("filing_group")
        if group and not rec["is_amendment"]:
            groups[group["id"]].append(rec)
    assert groups
    for members in groups.values():
        streets = [json.loads(rec["source_details"])["street_address_text"].casefold().strip()
                   for rec in members]
        assert len(set(streets)) == len(streets)
        assert len({(rec["employer_name"].casefold(), rec["notice_date"], rec["effective_date"])
                    for rec in members}) == 1
    # A site printed in both logs is one entry plus a duplicate capture.
    pointers = {rec["source_notice_id"] for rec in admitted}
    assert all(related[item["source_row"]] in pointers
               for item in held if item["reason"] == "duplicate_agency_capture")


def test_iowa_amendment_with_unique_parent_is_a_later_version():
    rows = extract(SOURCE) + extract_historical(SOURCE)
    admitted, held, _, related = project(rows)
    notices = {rec["dedupe_key"]: rec for rec in admitted if not rec["is_amendment"]}
    versions = [rec for rec in admitted if rec["is_amendment"]]
    assert versions
    by_row = {row["source_row"]: row for row in rows}
    for version in versions:
        parent = notices[version["dedupe_key"]]
        amendment = json.loads(version["source_details"])["amendment"]
        row = by_row[amendment["source_row"]]
        assert related[row["source_row"]] == parent["source_notice_id"]
        assert version["source_identity"] == parent["source_identity"]
        assert version["notice_date"] == parent["notice_date"] <= row["notice_date"]
        if "additional" in row["notice_type_text"].casefold():
            # Incremental-or-total is unstated: the parent's figures stand.
            assert amendment["applied_to_notice"] is False
            assert (version["effective_date"], version["employees_affected"]) == (
                parent["effective_date"], parent["employees_affected"])
        else:
            assert (version["effective_date"], version["employees_affected"]) == (
                row["effective_date"], row["workers_reported"] or None)
        assert " ".join(row["street_address_text"].casefold().split()) == " ".join(
            json.loads(parent["source_details"])["street_address_text"].casefold().split())
    # Versions follow their parent in the batch, so ingest makes them current.
    order = [rec["dedupe_key"] for rec in admitted]
    assert all(order.index(v["dedupe_key"]) < admitted.index(v) for v in versions)
