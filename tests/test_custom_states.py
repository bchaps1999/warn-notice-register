"""Normalization tests for our custom-adapter states, on trimmed real scrapes."""

from pathlib import Path

import pytest

from warnlive.normalize.engine import normalize_file

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


@pytest.mark.parametrize("state", ["ma", "mn", "nc", "nv"])
def test_custom_state_normalizes(state):
    result = normalize_file(state, FIXTURES, f"https://example.gov/{state}")
    assert result.raw_rows >= 10
    assert result.failure_rate <= 0.10, result.failure_examples
    records = result.records
    assert all(r["state"] == state.upper() for r in records)
    with_employer = sum(1 for r in records if r["employer_name"])
    assert with_employer / len(records) >= 0.95
    with_date = [r for r in records if r["notice_date"]]
    assert len(with_date) / len(records) >= 0.80


def test_custom_transformer_resolution():
    """Custom transformers must shadow warn-transformer for these states."""
    from warnlive.normalize.engine import get_transformer_class

    for state in ["ma", "mn", "nc", "nv", "sc"]:
        cls = get_transformer_class(state)
        assert cls.__module__ == f"warnlive.normalize.custom.{state}"


def test_massachusetts_date_lists_keep_the_first_date():
    from warnlive.normalize.custom.ma import Transformer

    transformer = Transformer(FIXTURES)
    assert transformer.transform_date("5/31/22-11/18/22") == "2022-05-31"
    assert transformer.transform_date("6/30/2022; 7/31/2022") == "2022-06-30"


def test_south_carolina_current_rows_keep_notice_range_address_and_action():
    from warnlive.fetch.custom.sc import _current_rows

    rows = _current_rows([
        ["International Paper", "Georgetown", "2/18/2026",
         "5/1/2026 - 12/31/2026", "126", "Permanent Closure",
         "1480 International Dr., Georgetown, SC 29440"],
    ])
    assert rows == [{
        "company": "International Paper", "county": "Georgetown",
        "notice_date": "2/18/2026", "effective_date": "5/1/2026",
        "effective_end_date": "12/31/2026", "impacted": "126",
        "action_type": "Permanent Closure",
        "address": "1480 International Dr., Georgetown, SC 29440",
    }]


def test_south_carolina_legacy_date_is_not_a_notice_date(tmp_path):
    raw = tmp_path / "sc.csv"
    raw.write_text(
        "company,county,notice_date,effective_date,effective_end_date,impacted,action_type,address,legacy_date,naics,source\n"
        "Old Employer,Richland,,,,42,,,4/1/2020,541611,sc/2020.pdf\n"
    )
    result = normalize_file("sc", tmp_path, "https://example.gov/sc")
    assert result.failed_rows == 0
    assert result.records[0]["notice_date"] is None
    assert result.records[0]["effective_date"] == "2020-04-01"


def test_south_carolina_cached_2026_report_has_distinct_dates_and_fields():
    """A smoke check for the real PDF kept in the shared fetch cache."""
    from warnlive.fetch.custom.sc import parse_pdf

    path = Path("workdir/cache/sc/2026.pdf")
    if not path.exists():
        pytest.skip("SC cached PDF is not available")
    rows = parse_pdf(path)
    assert len(rows) == 26
    paper = next(row for row in rows if row["company"] == "International Paper Company")
    assert paper["notice_date"] == "2/18/2026"
    assert paper["effective_date"] == "5/1/2026"
    assert paper["effective_end_date"] == "12/31/2026"
    assert paper["action_type"] == "Permanent Closure"
    assert paper["address"].endswith("SC 29440")
    # The PDF visually overprints these notice dates with "Multiple
    # Counties"; extracting the table alone used to drop both records.
    statewide = next(row for row in rows if row["company"].startswith("SMBC"))
    assert statewide["county"] == "Statewide - Multiple Counties"
    assert statewide["notice_date"] == "1/8/2026"


def test_south_carolina_cached_reports_do_not_lose_legacy_rows():
    """Counts are the existing raw snapshot's per-report population.

    This catches subtle PDF extraction changes: SC's legacy layouts use bare
    month/year and malformed double-slash dates, while newer reports have
    county labels that overprint notice-date glyphs.
    """
    from warnlive.fetch.custom.sc import parse_pdf

    cache = Path("workdir/cache/sc")
    expected = {
        "2013": 31, "2014": 20, "2015": 30, "2016": 31, "2017": 29,
        "2018": 34, "2019": 45, "2020": 154, "2021": 25, "2022": 34,
        "2023": 47, "2024": 56, "2025": 37, "2026": 26,
    }
    if not all((cache / f"{year}.pdf").exists() for year in expected):
        pytest.skip("full SC cached-report set is not available")
    assert {
        year: len(parse_pdf(cache / f"{year}.pdf")) for year in expected
    } == expected


def test_south_carolina_cached_2026_rows_normalize_without_date_role_swap(tmp_path):
    import csv
    from warnlive.fetch.custom.sc import COLUMNS, parse_pdf

    path = Path("workdir/cache/sc/2026.pdf")
    if not path.exists():
        pytest.skip("SC cached PDF is not available")
    rows = parse_pdf(path)
    with (tmp_path / "sc.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    result = normalize_file("sc", tmp_path, "https://example.gov/sc")
    assert result.raw_rows == len(result.records) == 26
    assert result.failed_rows == 0
    paper = next(r for r in result.records if r["employer_name"] == "International Paper Company")
    assert paper["notice_date"] == "2026-02-18"
    assert paper["effective_date"] == "2026-05-01"
    assert paper["effective_date_end"] == "2026-12-31"


def test_south_carolina_conflicting_undated_events_block_ingest(tmp_path):
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "sc.csv"
    raw.write_text("company\nAcme\n")
    rec = {
        "employer_name": "Acme", "notice_date": None,
        "effective_date": "2026-01-01", "employees_affected": 10,
        "dedupe_key": "same-key",
    }
    norm = NormalizeResult(
        state="SC", raw_rows=2,
        records=[rec, {**rec, "effective_date": "2026-09-01"}],
    )
    result = verify_state(load_registry()["sc"], raw, norm)
    check = next(c for c in result.checks if c.name == "sc_event_identity")
    assert check.outcome == "fail"


def test_georgia_conflicting_filing_ids_block_ingest(tmp_path):
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "ga.csv"
    raw.write_text("company\nAcme\n")
    rec = {
        "employer_name": "Acme", "notice_date": None,
        "effective_date": "2026-01-01", "employees_affected": 10,
        "dedupe_key": "same-key", "source_identity": "GA:1",
    }
    norm = NormalizeResult(
        state="GA", raw_rows=2,
        records=[rec, {**rec, "source_identity": "GA:2"}],
    )
    result = verify_state(load_registry()["ga"], raw, norm)
    check = next(c for c in result.checks if c.name == "ga_filing_identity")
    assert check.outcome == "fail"


def test_iowa_distinct_phases_and_revisions_block_ingest(tmp_path):
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "ia.csv"
    raw.write_text("Company\nAcme\n")
    rec = {
        "employer_name": "Acme", "notice_date": "2026-01-20",
        "effective_date": "2026-04-01", "employees_affected": 10,
        "layoff_type": "closure", "is_amendment": 0,
        "dedupe_key": "same-key",
        "raw_extra": '{"Address Line 1": "1 Main St"}',
    }
    norm = NormalizeResult(
        state="IA", raw_rows=3,
        records=[rec, {**rec, "effective_date": "2026-05-01"},
                 {**rec, "employees_affected": 12}],
    )
    result = verify_state(load_registry()["ia"], raw, norm)
    check = next(c for c in result.checks if c.name == "ia_phase_identity")
    assert check.outcome == "fail"


def test_iowa_exact_repeated_rows_do_not_fail_identity(tmp_path):
    from warnlive.normalize.engine import NormalizeResult
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    raw = tmp_path / "ia.csv"
    raw.write_text("Company\nAcme\n")
    rec = {
        "employer_name": "Acme", "notice_date": "2026-01-20",
        "effective_date": "2026-04-01", "employees_affected": 10,
        "layoff_type": "closure", "is_amendment": 0,
        "dedupe_key": "same-key",
        "raw_extra": '{"Address Line 1": "1 Main St"}',
    }
    result = verify_state(
        load_registry()["ia"], raw,
        NormalizeResult(state="IA", raw_rows=2, records=[rec, rec.copy()]),
    )
    check = next(c for c in result.checks if c.name == "ia_phase_identity")
    assert check.outcome == "pass"
