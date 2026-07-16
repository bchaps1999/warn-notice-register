"""Model protocol and predictive density container."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import numpy as np
import pandas as pd

from uiforecast.ingest.seasonal import FactorForecast


@dataclass
class InfoSet:
    """Everything knowable at the forecast origin. Built strictly as-of."""

    origin: datetime
    target_week: pd.Timestamp          # week-ending Saturday being nowcast
    nsa_history: pd.Series             # ICNSA vintage at origin (excludes target)
    sa_history: pd.Series              # ICSA vintage at origin
    factors: pd.DataFrame              # implied factors, releases < origin
    factor_fc: FactorForecast          # factor forecast for target week
    warn_national: pd.DataFrame | None = None  # national aggregate as-of origin
    meta: dict = field(default_factory=dict)


@dataclass
class PredictiveDensity:
    """Empirical predictive distribution for the SA advance print (units:
    persons, resolution 1,000s)."""

    sa_draws: np.ndarray
    nsa_point: float | None = None

    @property
    def mean(self) -> float:
        return float(np.mean(self.sa_draws))

    def quantile(self, q) -> float | np.ndarray:
        return np.quantile(self.sa_draws, q)

    def cdf(self, x: float) -> float:
        return float(np.mean(self.sa_draws <= x))

    def sample(self, n: int, rng: np.random.Generator) -> np.ndarray:
        return rng.choice(self.sa_draws, size=n)


class ForecastModel(Protocol):
    name: str

    def fit(self, info: InfoSet) -> None: ...

    def predict(self) -> PredictiveDensity: ...
