"""Diebold-Mariano test with Harvey-Leybourne-Newbold small-sample correction."""

from __future__ import annotations

import numpy as np
from scipy import stats


def dm_test(
    loss_a: np.ndarray,
    loss_b: np.ndarray,
    h: int = 1,
    alternative: str = "less",
) -> tuple[float, float]:
    """DM test on loss differential d = loss_a - loss_b.

    alternative='less': H1 is that model A has LOWER loss than model B.
    Returns (statistic, p_value). h = forecast horizon (1 for nowcasts).
    """
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    d = d[~np.isnan(d)]
    n = len(d)
    if n < 10:
        return np.nan, np.nan
    dbar = d.mean()
    # HAC variance with truncation lag h-1 (Newey-West weights)
    gamma0 = np.mean((d - dbar) ** 2)
    var = gamma0
    for k in range(1, h):
        cov = np.mean((d[k:] - dbar) * (d[:-k] - dbar))
        var += 2 * (1 - k / h) * cov
    if var <= 0:
        return np.nan, np.nan
    dm = dbar / np.sqrt(var / n)
    # HLN correction
    hln = dm * np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    pt = stats.t.cdf(hln, df=n - 1)
    if alternative == "less":
        p = pt
    elif alternative == "greater":
        p = 1 - pt
    else:
        p = 2 * min(pt, 1 - pt)
    return float(hln), float(p)
