"""Louisiana Wayback archive of the 2007-2024 annual WARN tables."""

import json
import shutil
from functools import lru_cache
from pathlib import Path

import pytest

from warnlive.migrate.la_source import (
    existing_events, extract, project_archive, read_archive, split_company_cell,
)

ROOT = Path(__file__).resolve().parents[1] / "data/source_snapshots/la"
ARCHIVE = ROOT / "wayback-2026-09-30"


@lru_cache(maxsize=1)
def _projected():
    return project_archive(ARCHIVE, existing_events(extract(ROOT)))


def test_every_table_row_is_admitted_or_held():
    records, held, report = _projected()
    assert report["source_rows"] == 587 == len(records) + len(held)
    assert report["hold_reasons"] == {
        "official_source_continuation_row": 11, "official_source_rescinded": 7,
        "official_source_update_unresolved": 25,
    }
    assert (report["admitted"], report["admitted_missing_workers"],
            report["admitted_missing_notice_date"]) == (544, 16, 2)
    assert len({rec["dedupe_key"] for rec in records}) == len(records)
    assert all(item["raw_extra"] and item["source_row_sha256"] for item in held)
    continuation = [item for item in held if item["reason"] == "official_source_continuation_row"]
    assert all(item["related_source_row"].startswith("agency/la_archive/") for item in continuation)


def test_dates_ranges_and_counts_keep_the_table_text():
    records, _, _ = _projected()
    by_id = {rec["source_identity"]: rec for rec in records}
    macys = by_id["LA:archive:2007:p1:r2"]
    assert (macys["employer_name"], macys["notice_date"], macys["effective_date"],
            macys["employees_affected"]) == ("Macy's", "2007-12-31", "2008-03-18", 66)
    details = json.loads(macys["source_details"])
    assert details["address_role"] == "unverified" and macys["location"] is None
    assert details["address_text"].startswith("640 W Prien Lake Road")
    coast = by_id["LA:archive:2015:p1:r10"]
    assert (coast["effective_date"], coast["effective_date_end"]) == ("2015-03-05", "2015-04-22")
    # "125* *Only one employee affected in Louisiana" is not a whole count.
    lost_boys = next(rec for rec in records if rec["employer_name"].startswith("Lost Boys"))
    assert lost_boys["employees_affected"] is None
    # Two dates in the notice cell leave the notice date blank.
    assert {rec["employer_name"] for rec in records if rec["notice_date"] is None} == {
        "Helmerich & Payne International Drilling Green Canyon 65 (Bullwinkle) "
        "Platform Offshore Louisiana", "Garden City Group, LLC"}


def test_status_rows_are_held_and_sites_stay_distinct():
    records, held, _ = _projected()
    reasons = {json.loads(item["raw_extra"])[0].split("\n")[0]: item["reason"] for item in held}
    assert reasons["Noranda Aluminum"] == "official_source_rescinded"
    assert reasons["Ingevity Corporation"] == "official_source_update_unresolved"
    # Hostess Brands lists 19 sites on one notice date; each site is its own row.
    assert sum(rec["employer_name"] == "Hostess Brands" for rec in records) == 19


def test_known_events_are_held_and_company_split():
    records, _, _ = _projected()
    macys = next(rec for rec in records if rec["source_identity"] == "LA:archive:2007:p1:r2")
    known = {("2007-12-31", 66, "macys")}
    again, held, _ = project_archive(ARCHIVE, known)
    assert macys["dedupe_key"] not in {rec["dedupe_key"] for rec in again}
    assert any(item["reason"] == "already_represented_la_row" for item in held)
    assert split_company_cell("Lockheed Martin Space\nSystems Company\n13800 Old Gentilly Road\n"
                              "New Orleans, LA 70129") == (
        "Lockheed Martin Space Systems Company", "13800 Old Gentilly Road\nNew Orleans, LA 70129")


def test_louisiana_archive_drift_fails_closed(tmp_path):
    shutil.copytree(ARCHIVE, tmp_path, dirs_exist_ok=True)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    name = manifest["artifacts"][0]["file"]
    (tmp_path / name).write_bytes((tmp_path / name).read_bytes() + b"\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        read_archive(tmp_path)
    shutil.copy2(ARCHIVE / name, tmp_path / name)
    manifest["artifacts"][0]["data_rows"] += 1
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="row count changed"):
        read_archive(tmp_path)
