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

    for state in ["ma", "mn", "nc", "nv"]:
        cls = get_transformer_class(state)
        assert cls.__module__ == f"warnlive.normalize.custom.{state}"
