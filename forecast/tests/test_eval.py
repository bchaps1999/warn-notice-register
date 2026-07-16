import numpy as np

from uiforecast.eval.brackets import (
    bracket_log_score,
    bracket_probs,
    realized_bracket,
    synthetic_brackets,
)
from uiforecast.eval.dm import dm_test
from uiforecast.eval.metrics import crps_sample, interval_coverage, log_score_kde, pit


def test_crps_degenerate_equals_abs_error():
    draws = np.full(1000, 220_000.0)
    assert abs(crps_sample(draws, 225_000.0) - 5000.0) < 1e-6


def test_crps_matches_closed_form_normal():
    # CRPS of N(0,1) at y=0 is sigma*(2/sqrt(2pi) - 1/sqrt(pi)) ~= 0.2337
    rng = np.random.default_rng(0)
    draws = rng.normal(0, 1, 200_000)
    assert abs(crps_sample(draws, 0.0) - 0.23370) < 0.005


def test_pit_and_coverage():
    rng = np.random.default_rng(1)
    draws = rng.normal(0, 1, 10_000)
    assert abs(pit(draws, 0.0) - 0.5) < 0.02
    pits = rng.uniform(0, 1, 10_000)
    assert abs(interval_coverage(pits, 0.9) - 0.9) < 0.02


def test_log_score_orders_correctly():
    rng = np.random.default_rng(2)
    good = rng.normal(220_000, 5_000, 20_000)
    bad = rng.normal(250_000, 5_000, 20_000)
    assert log_score_kde(good, 221_000) < log_score_kde(bad, 221_000)


def test_brackets_partition_and_score():
    bs = synthetic_brackets(223_400)  # anchors to 220k
    probs = bracket_probs(np.random.default_rng(3).normal(225_000, 8_000, 50_000), bs)
    assert abs(probs.sum() - 1.0) < 1e-9
    assert realized_bracket(224_000, bs) == "220-230k"
    assert realized_bracket(1e7, bs).startswith(">=")
    ls = bracket_log_score(
        np.random.default_rng(4).normal(225_000, 8_000, 50_000), 224_000, bs
    )
    assert 0 < ls < 3


def test_dm_detects_better_model():
    rng = np.random.default_rng(5)
    base_err = rng.normal(0, 1, 300) ** 2
    better = base_err * 0.5 + rng.normal(0, 0.01, 300) ** 2
    stat, p = dm_test(better, base_err, alternative="less")
    assert p < 0.01
    stat2, p2 = dm_test(base_err, better, alternative="less")
    assert p2 > 0.9
