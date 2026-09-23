"""Iowa review worklists expose source evidence without deciding event identity."""

import json
import sys

import pytest

from warnlive.migrate.ia_review import build, main


def _row(source, n, **changes):
    return {
        "source_artifact": f"agency/ia/{source}",
        "source_row": f"{source}:r{n}", "source_row_sha256": f"hash-{source}-{n}",
        "company_text": "Acme", "city_text": "Ames",
        "street_address_text": "1 Main St", "county_text": "Story",
        "address_state_text": "IA", "notice_type_text": "Mass Layoff",
        "notice_date": "2022-01-01", "effective_date": "2022-03-01",
        "workers_reported": 20,
    } | changes


def _report(current, historical):
    return {
        "format": "warn-ia-correspondence-v1",
        "inputs": {"bundle_sha256": "bundle", "candidate_ia_fingerprint": "candidate"},
        "official": {"rows": [{"official": row} for row in current]},
        "historical_official": {"rows": [{"official": row} for row in historical]},
    }


def test_unique_changed_and_ambiguous_correspondence_and_history_order():
    current = [
        _row("current", 2, company_text="Exact"),
        _row("current", 1, company_text="Changed", notice_type_text="Amendment",
             workers_reported=25),
        _row("current", 3, company_text="Ambiguous"),
    ]
    historical = [
        _row("historical", 1, company_text="Exact"),
        _row("historical", 2, company_text="Changed"),
        _row("historical", 3, company_text="Ambiguous", notice_date="2021-01-01"),
        _row("historical", 4, company_text="Ambiguous", notice_date="2021-02-01"),
    ]
    result = build(_report(current, historical))
    assert result["counts"]["cross_source_buckets"] == {
        "ambiguous": 3, "strong_unique": 2, "weak_unique": 2,
    }
    by_row = {item["source_row"]: item for item in result["cross_source"]}
    assert by_row["current:r1"]["candidates"][0]["changed_fields"] == [
        "notice_type_text", "workers_reported",
    ]
    assert len(by_row["current:r3"]["candidates"]) == 2
    assert result["inputs"]["candidate_ia_fingerprint"] == "candidate"
    ambiguous_history = next(item for item in result["amendment_histories"]
                             if item["employer_key"] == "ambiguous")
    assert [item["source_row"] for item in ambiguous_history["rows"]] == [
        "historical:r3", "historical:r4", "current:r3",
    ]
    assert build(_report(current, historical)) == result


def test_anomaly_flags_duplicate_malformed_city_state_and_notice_type():
    row = _row("current", 1, city_text="", address_state_text="CA",
               notice_date=None, notice_type_text="Mayss Layoff",
               layout_issues=["invalid_notice_date"])
    duplicate = _row("current", 2, source_row_sha256=row["source_row_sha256"])
    result = build(_report([row, duplicate], []))
    flags = {item["source_row"]: item["flags"] for item in result["anomalies"]}
    assert flags["current:r1"] == [
        "exact_duplicate_row", "malformed_date", "missing_city",
        "non_ia_address_state", "source_layout:invalid_notice_date",
        "unrecognized_notice_type",
    ]
    assert flags["current:r2"] == ["exact_duplicate_row"]


def test_repeated_agency_spelling_for_cnh_is_a_recognized_label():
    row = _row("current", 1, notice_type_text="Amendement - Change in Number")
    assert build(_report([row], []))["anomalies"] == []


def test_strong_unique_requires_reciprocal_exact_uniqueness():
    first = _row("current", 1)
    second = _row("current", 2)
    old = _row("historical", 1)
    result = build(_report([first, second], [old]))
    assert result["counts"]["cross_source_buckets"] == {"ambiguous": 3}


def test_additional_workers_singleton_requires_base_review():
    row = _row("current", 1, notice_type_text="Mass Layoff - Additional Employees")
    result = build(_report([row], []))
    assert result["amendment_histories"][0]["rows"][0]["source_row"] == row["source_row"]


def test_all_reported_fields_are_compared_after_exact_fact_match():
    current = _row("current", 1, industry_text="Manufacturing",
                   local_workforce_area_text="Area A", postal_code_text="50010")
    historical = _row("historical", 1, industry_text="Retail",
                      local_workforce_area_text="Area B", postal_code_text="50011")
    result = build(_report([current], [historical]))
    assert result["counts"]["cross_source_buckets"] == {"changed_fields": 2}
    assert result["cross_source"][0]["candidates"][0]["changed_fields"] == [
        "postal_code_text", "local_workforce_area_text", "industry_text",
    ]


def test_workbook_numeric_zip_matches_pdf_printed_zip():
    current = _row("current", 1, postal_code_text=50010)
    historical = _row("historical", 1, postal_code_text="50010")
    assert build(_report([current], [historical]))["counts"]["cross_source_buckets"] == {
        "strong_unique": 2,
    }


def test_refuses_missing_sources_or_duplicate_pointers():
    with pytest.raises(ValueError, match="both frozen official"):
        build({"format": "warn-ia-correspondence-v1", "official": {"rows": []}})
    row = _row("current", 1)
    with pytest.raises(ValueError, match="duplicate Iowa source pointer"):
        build(_report([row, row], []))


def test_cli_writes_once_and_refuses_overwrite(tmp_path, monkeypatch):
    input_path = tmp_path / "report.json"
    output_path = tmp_path / "worklist.json"
    input_path.write_text(json.dumps(_report([_row("current", 1)], [])))
    monkeypatch.setattr(sys, "argv", ["ia_review", "--report", str(input_path),
                                      "--out", str(output_path)])
    main()
    before = output_path.read_bytes()
    assert json.loads(before)["counts"]["official_current_rows"] == 1
    with pytest.raises(FileExistsError):
        main()
    assert output_path.read_bytes() == before
