import csv
import json

import pytest

from warnlive.migrate.job_portal_source import (
    PORTALS, capture, evidence_manifest, freeze_capture, parse_detail, parse_listing,
    project, stage_raw,
)
from warnlive.normalize.engine import normalize_file

AREAS = {
    "az": "7 - ARIZONA@WORK - Maricopa County",
    "me": "3 - Central Western Local Area 3",
    "vt": "00 - Vermont Workforce Investment Area",
    "de": "1 - Statewide",
}


def _listing(rows, pages=1):
    body = "".join(
        f'<tr><td><a href="/search/warn_lookups/{number}">{employer}</a></td>'
        f"<td>{city}</td><td>{zip_code}</td><td>{area}</td><td>{day}</td><td>WARN</td></tr>"
        for number, employer, city, zip_code, area, day in rows
    )
    return f"""
    <table role="grid"><caption>Sortable list of WARN entries</caption>
      <thead><tr><th>Employer</th><th>City</th><th>ZIP</th><th>LWIB Area</th>
        <th>Notice Date</th><th>WARN Type</th></tr></thead>
      <tbody>{body}</tbody></table>
    <div aria-label="Pagination"><em aria-current="page">1</em>
      <a aria-label="Page {pages}" href="?page={pages}">{pages}</a></div>
    """.encode()


def _detail(employer, day, workers, address=None):
    fields = [("Company Name", employer)]
    if address is not None:
        fields.append(("Address", address))
    fields += [("Notice Date", day), ("Number of Employees Affected", workers)]
    return ('<div class="definition-list">' + "".join(
        f'<h3 class="definition-list__title">{name}</h3>'
        f'<p class="definition-list__definition">{value}</p>' for name, value in fields
    ) + "</div>").encode()


@pytest.mark.parametrize("postal", ["az", "me", "vt", "de"])
def test_portal_listing_and_detail_parse_per_state(postal):
    portal = PORTALS[postal]
    rows, pages = parse_listing(_listing(
        [("954", "Block, Inc.", "Oakland", "94612", AREAS[postal], "Feb 26, 2026")], pages=31,
    ), portal)
    assert pages == 31
    assert rows == [{
        "employer": "Block, Inc.", "city": "Oakland", "zip": "94612",
        "lwib_area": AREAS[postal], "notice_date": "Feb 26, 2026", "warn_type": "WARN",
        "record_number": "954",
        "detail_page_url": f"{portal.base}/search/warn_lookups/954",
    }]
    assert parse_detail(_detail("Block, Inc.", "Feb 26, 2026", "83", "1955 Broadway"), portal) == {
        "Company Name": "Block, Inc.", "Address": "1955 Broadway",
        "Notice Date": "Feb 26, 2026", "Number of Employees Affected": "83",
    }
    with pytest.raises(ValueError, match=f"^{portal.name} WARN filter returned other type"):
        parse_listing(_listing([("1", "A", "", "", "x", "Jan 01, 2020")]).replace(
            b"<td>WARN</td>", b"<td>Non-WARN</td>"), portal)
    assert portal.search == (
        f"{portal.base}/search/warn_lookups?commit=Search&q%5Bnotice_eq%5D=true")


def _capture(tmp_path, postal, rows, details):
    out = tmp_path / postal
    (out / "listings").mkdir(parents=True)
    (out / "details").mkdir()
    (out / "listings" / "page-001.html").write_bytes(_listing(rows))
    for number, content in details.items():
        (out / "details" / f"{int(number):06d}.html").write_bytes(content)
    # Every page is already saved, so capture reads them without a request.
    manifest = capture(out, PORTALS[postal], delay=1.5)
    assert manifest["listed_warn_rows"] == manifest["detail_pages"] == len(rows)
    return out


def test_portal_projection_matches_live_staging_and_holds_known_records(tmp_path):
    area = AREAS["az"]
    rows = [
        ("10", "Old Released Co", "Phoenix", "85001", area, "Jan 05, 2015"),
        ("11", "New Co", "Tucson", "85701", area, "Mar 02, 2020"),
        ("12", "Twin Sites", "Mesa", "85201", area, "Apr 30, 2015"),
        ("13", "Twin Sites", "Yuma", "85364", area, "Apr 30, 2015"),
        ("14", "Undated Co", "Mesa", "85201", area, ""),
    ]
    details = {
        "10": _detail("Old Released Co", "Jan 05, 2015", "40"),
        "11": _detail("New Co", "Mar 02, 2020", "1,200", "1 Main St; Tucson, Arizona 85701"),
        "12": _detail("Twin Sites", "Apr 30, 2015", "10"),
        "13": _detail("Twin Sites", "Apr 30, 2015", "10"),
        "14": _detail("Undated Co", "", "5"),
    }
    portal = PORTALS["az"]
    out = _capture(tmp_path, "az", rows, details)
    assert stage_raw(out, portal) == {
        "listed": 5, "staged": 2, "held": 3, "unparseable_worker_counts": 0}
    holds = {json.loads(line)["record_number"]: json.loads(line)["reason"]
             for line in (out / "holds.jsonl").read_text().splitlines()}
    assert holds == {"12": "same_employer_day_event_identity_unresolved",
                     "13": "same_employer_day_event_identity_unresolved",
                     "14": "missing_notice_date"}
    # The live collector's raw CSV: what the scheduled scrape normalizes.
    live = {json.loads(rec["raw_extra"])["record_number"]: rec["dedupe_key"]
            for rec in normalize_file("az", out, "https://example.gov", "2026-09-30").records}
    assert set(live) == {"10", "11"}

    table = tmp_path / "table"
    table.mkdir()
    archive = table / "az-portal-evidence.tar.gz"
    freeze_capture(out, archive, portal)
    (table / "manifest.json").write_text(json.dumps(evidence_manifest(archive, portal, {})))
    records, held, report = project(
        table, portal, {"10", "13"}, set(), "https://example.gov", "2026-09-30")
    # Same key as the live collector gives the record; known records held.
    assert [(json.loads(r["raw_extra"])["record_number"], r["dedupe_key"]) for r in records] == [
        ("11", live["11"])]
    assert records[0]["employees_affected"] == 1200
    by_number = {item["source_record_number"]: item for item in held}
    assert {n: item["reason"] for n, item in by_number.items()} == {
        "10": "record_in_earlier_capture",
        "12": "same_employer_day_event_identity_unresolved",
        "13": "record_in_earlier_capture",
        "14": "missing_notice_date",
    }
    assert by_number["13"]["staging_hold_reason"] == "same_employer_day_event_identity_unresolved"
    assert report["listed"] == 5 and report["admitted"] == 1

    # An already-admitted key is held, never merged.
    records, held, _ = project(
        table, portal, set(), {live["11"]}, "https://example.gov", "2026-09-30")
    assert {r["dedupe_key"] for r in records} == {live["10"]}
    assert "same_key_as_admitted_notice" in {item["reason"] for item in held}

    archive.write_bytes(archive.read_bytes() + b"x")
    with pytest.raises(ValueError, match="checksum changed"):
        project(table, portal, set(), set(), None, "2026-09-30")


def test_portal_record_keeps_legacy_key_across_captures(tmp_path):
    # A released AZ notice was keyed from the upstream collector's CSV (same
    # columns); the portal collector's row for the same record gets that key.
    header = ["employer", "notice_date", "number_of_employees_affected", "warn_type",
              "city", "zip", "lwib_area", "address", "record_number", "detail_page_url"]
    row = ["Block, Inc.", "Feb 26, 2026", "83", "WARN", "Oakland", "94612", AREAS["az"],
           "1955 Broadway, Suite 600; Oakland, California 94612", "954",
           "https://www.azjobconnection.gov/search/warn_lookups/954"]
    for name, workers in (("july", "83"), ("portal", "90")):
        folder = tmp_path / name
        folder.mkdir()
        with (folder / "az.csv").open("w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            writer.writerow(row[:2] + [workers] + row[3:])
    keys = {name: normalize_file("az", tmp_path / name, None, "2026-09-30").records[0]["dedupe_key"]
            for name in ("july", "portal")}
    assert keys["july"] == keys["portal"]
