import csv
import hashlib
import json
import tarfile

import pytest

from warnlive.migrate.ks_portal_source import (
    freeze_capture, parse_detail, parse_listing, stage_raw, suspected_variant_pairs,
)


def test_kansas_listing_requires_warn_filter_and_stable_ids():
    html = b"""
    <table role="grid"><caption>Sortable list of WARN entries</caption>
      <thead><tr><th>Employer</th><th>City</th><th>ZIP</th><th>LWIB Area</th>
        <th>Notice Date</th><th>WARN Type</th></tr></thead>
      <tbody><tr><td><a href="/search/warn_lookups/2300">Acme</a></td>
        <td>Iola</td><td>66749</td><td>Area V</td><td>Feb 23, 2026</td>
        <td>WARN</td></tr></tbody>
    </table>
    <div aria-label="Pagination"><em aria-current="page">1</em>
      <a aria-label="Page 37" href="?page=37">37</a></div>
    """
    rows, pages = parse_listing(html)
    assert pages == 37
    assert rows == [{
        "employer": "Acme", "city": "Iola", "zip": "66749",
        "lwib_area": "Area V", "notice_date": "Feb 23, 2026",
        "warn_type": "WARN", "record_number": "2300",
        "detail_page_url": "https://www.kansasworks.com/search/warn_lookups/2300",
    }]
    with pytest.raises(ValueError, match="other type"):
        parse_listing(html.replace(b"<td>WARN</td>", b"<td>Non-WARN</td>"))


def test_kansas_detail_retains_source_field_names():
    html = b"""
    <div class="definition-list">
      <h3 class="definition-list__title">Company Name</h3>
      <p class="definition-list__definition">Acme</p>
      <h3 class="definition-list__title">Notice Date</h3>
      <p class="definition-list__definition">Feb 23, 2026</p>
      <h3 class="definition-list__title">Number of Employees Affected</h3>
      <p class="definition-list__definition">130</p>
    </div>
    """
    assert parse_detail(html) == {
        "Company Name": "Acme", "Notice Date": "Feb 23, 2026",
        "Number of Employees Affected": "130",
    }
    with pytest.raises(ValueError, match="affected-worker"):
        parse_detail(html.replace(b"Number of Employees Affected", b"Other field"))


def test_kansas_staging_checks_original_pages_before_writing_raw(tmp_path):
    listing = b"""
    <table role="grid"><caption>Sortable list of WARN entries</caption>
      <thead><tr><th>Employer</th><th>City</th><th>ZIP</th><th>LWIB Area</th>
        <th>Notice Date</th><th>WARN Type</th></tr></thead>
      <tbody><tr><td><a href="/search/warn_lookups/2300">Acme</a></td>
        <td>Iola</td><td>66749</td><td>Area V</td><td>Feb 23, 2026</td>
        <td>WARN</td></tr></tbody></table>
    """
    detail = b"""
    <div class="definition-list">
      <h3 class="definition-list__title">Company Name</h3>
      <p class="definition-list__definition">Acme</p>
      <h3 class="definition-list__title">Notice Date</h3>
      <p class="definition-list__definition">Feb 23, 2026</p>
      <h3 class="definition-list__title">Number of Employees Affected</h3>
      <p class="definition-list__definition">1,300</p>
    </div>
    """
    files = {"listings/page-001.html": listing, "details/002300.html": detail}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    rows, _ = parse_listing(listing)
    rows[0]["detail"] = parse_detail(detail)
    manifest = {
        "listing_pages": 1, "listed_warn_rows": 1, "detail_pages": 1,
        "files": [{"path": name, "sha256": hashlib.sha256(content).hexdigest()}
                  for name, content in files.items()],
    }
    (tmp_path / "inventory.json").write_text(json.dumps({"manifest": manifest, "rows": rows}))
    assert stage_raw(tmp_path) == {
        "listed": 1, "staged": 1, "held": 0, "unparseable_worker_counts": 0,
    }
    with (tmp_path / "ks.csv").open() as handle:
        staged = list(csv.DictReader(handle))
    assert staged[0]["record_number"] == "2300"
    assert staged[0]["number_of_employees_affected"] == "1300"
    first = freeze_capture(tmp_path, tmp_path.parent / "first.tar.gz")
    second = freeze_capture(tmp_path, tmp_path.parent / "second.tar.gz")
    assert first["archive_sha256"] == second["archive_sha256"]
    with tarfile.open(first["archive"], "r:gz") as archive:
        assert set(archive.getnames()) == {
            "inventory.json", "ks.csv", "holds.jsonl", "field_warnings.jsonl",
            "hold_policy.json", "provenance.json",
            "listings/page-001.html", "details/002300.html",
        }
    (tmp_path / "details" / "002300.html").write_bytes(detail + b"altered")
    with pytest.raises(ValueError, match="checksum changed"):
        stage_raw(tmp_path)


def test_kansas_staging_holds_same_employer_day_across_areas(tmp_path):
    listing = b"""
    <table role="grid"><caption>Sortable list of WARN entries</caption>
      <thead><tr><th>Employer</th><th>City</th><th>ZIP</th><th>LWIB Area</th>
        <th>Notice Date</th><th>WARN Type</th></tr></thead>
      <tbody>
        <tr><td><a href="/search/warn_lookups/315">Penske</a></td>
          <td></td><td></td><td>Area III</td><td>Jun 25, 2002</td><td>WARN</td></tr>
        <tr><td><a href="/search/warn_lookups/316">Penske</a></td>
          <td></td><td></td><td>Area II</td><td>Jun 25, 2002</td><td>WARN</td></tr>
      </tbody></table>
    """
    detail = b"""
    <div class="definition-list">
      <h3 class="definition-list__title">Company Name</h3>
      <p class="definition-list__definition">Penske</p>
      <h3 class="definition-list__title">Notice Date</h3>
      <p class="definition-list__definition">Jun 25, 2002</p>
      <h3 class="definition-list__title">Number of Employees Affected</h3>
      <p class="definition-list__definition">10</p>
    </div>
    """
    files = {"listings/page-001.html": listing,
             "details/000315.html": detail, "details/000316.html": detail}
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    rows, _ = parse_listing(listing)
    for row in rows:
        row["detail"] = parse_detail(detail)
    manifest = {
        "listing_pages": 1, "listed_warn_rows": 2, "detail_pages": 2,
        "files": [{"path": name, "sha256": hashlib.sha256(content).hexdigest()}
                  for name, content in files.items()],
    }
    (tmp_path / "inventory.json").write_text(json.dumps({"manifest": manifest, "rows": rows}))
    assert stage_raw(tmp_path)["held"] == 2
    holds = [json.loads(line) for line in (tmp_path / "holds.jsonl").read_text().splitlines()]
    assert {item["reason"] for item in holds} == {
        "same_employer_day_event_identity_unresolved"
    }
    assert all(item["related_record_numbers"] == ["315", "316"] for item in holds)


def test_kansas_variant_screen_holds_same_day_aliases_without_generic_name_match():
    rows = [{"record_number": str(i)} for i in range(1, 5)]
    details = [
        {"Company Name": "CertainTeed", "Notice Date": "Oct 31, 2006",
         "Number of Employees Affected": "120"},
        {"Company Name": "CertainTeed Corporation", "Notice Date": "Oct 31, 2006",
         "Number of Employees Affected": "120"},
        {"Company Name": "Emporia Rehabilitation Center", "Notice Date": "Feb 18, 2003",
         "Number of Employees Affected": "74"},
        {"Company Name": "Hoisington Rehabilitationl Center", "Notice Date": "Feb 18, 2003",
         "Number of Employees Affected": "64"},
    ]
    assert suspected_variant_pairs(rows, details) == {"1": ["1", "2"], "2": ["1", "2"]}
