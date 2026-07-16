from pathlib import Path

from warnlive.normalize.engine import _fold, normalize_file

FIXTURES = Path(__file__).parent / "fixtures" / "raw"


def test_normalize_ct_fixture():
    result = normalize_file("ct", FIXTURES, "https://example.gov/ct")
    assert result.raw_rows == 3
    # One row has a hopeless date not in CT's correction table -> counted failure
    assert result.failed_rows == 1
    assert len(result.records) == 2

    rec = result.records[0]
    assert rec["state"] == "CT"
    assert rec["employer_name"] == "Acme Manufacturing Inc."
    assert rec["notice_date"] == "2026-06-01"
    assert rec["employees_affected"] == 120
    assert rec["dedupe_key"] and rec["raw_record_hash"]
    assert rec["source_url"] == "https://example.gov/ct"

    # Same employer+date+location in rows 1 and 3 -> same dedupe key
    assert result.records[0]["dedupe_key"] != result.records[1]["dedupe_key"]


def test_fold_normalizes_for_dedupe_key_only():
    assert _fold("Acme Manufacturing, Inc.") == _fold("ACME MANUFACTURING LLC")
    assert _fold(None) == ""


def test_verify_state_on_fixture():
    from warnlive.registry import load_registry
    from warnlive.verify.harness import verify_state

    cfg = load_registry()["ct"]
    result = normalize_file("ct", FIXTURES, cfg.source_url)
    verification = verify_state(cfg, FIXTURES / "ct.csv", result)
    by_name = {c.name: c.outcome for c in verification.checks}
    assert by_name["fetch_ok"] == "pass"
    # Fixture has 3 rows, far below CT's min_rows threshold -> fail
    assert by_name["row_count"] == "fail"
    # 1/3 rows failed parse -> above 10% threshold -> fail
    assert by_name["parse_failures"] == "fail"
    assert verification.verdict == "failed"
