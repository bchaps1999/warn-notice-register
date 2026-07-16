"""Point and density scoring metrics."""

from __future__ import annotations

import numpy as np


def crps_sample(draws: np.ndarray, realized: float) -> float:
    """Sample-based CRPS (Gneiting-Raftery): E|X - y| - 0.5 E|X - X'|.

    Uses the sorted-sample identity for the second term, O(n log n).
    """
    x = np.sort(np.asarray(draws, dtype=float))
    n = len(x)
    term1 = np.mean(np.abs(x - realized))
    # E|X - X'| = 2/n^2 * sum_i (2i - n - 1) * x_(i)   (1-indexed)
    i = np.arange(1, n + 1)
    term2 = 2.0 / (n * n) * np.sum((2 * i - n - 1) * x)
    return float(term1 - 0.5 * term2)


def log_score_kde(draws: np.ndarray, realized: float, bw: float | None = None) -> float:
    """Negative log predictive density at the realized value (lower better),
    via Gaussian KDE with Silverman bandwidth on the draws."""
    x = np.asarray(draws, dtype=float)
    n = len(x)
    sd = np.std(x)
    if sd == 0:
        sd = 1.0
    h = bw or 1.06 * sd * n ** (-1 / 5)
    z = (realized - x) / h
    dens = np.mean(np.exp(-0.5 * z * z)) / (h * np.sqrt(2 * np.pi))
    return float(-np.log(max(dens, 1e-300)))


def pit(draws: np.ndarray, realized: float) -> float:
    """Probability integral transform value (should be U(0,1) if calibrated)."""
    return float(np.mean(np.asarray(draws) <= realized))


def interval_coverage(pits: np.ndarray, level: float) -> float:
    """Share of PIT values inside the central `level` interval."""
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    p = np.asarray(pits)
    return float(np.mean((p >= lo) & (p <= hi)))
