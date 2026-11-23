"""Synthetic daily demand generator (seeded, deterministic)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def generate_demand(n_series: int = 12, n_days: int = 540, start: str = "2024-01-01",
                    seed: int = 42) -> pd.DataFrame:
    """Return long-format frame: series_id, date, promo, y.

    Each series has its own base level, trend, weekly profile, yearly
    seasonality and promo uplift. Noise is multiplicative Poisson-like.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=n_days, freq="D")
    t = np.arange(n_days)
    frames = []
    for s in range(n_series):
        base = rng.uniform(30, 200)
        trend = rng.uniform(-0.02, 0.08)
        weekly = 1 + rng.uniform(0.1, 0.5) * np.sin(2 * np.pi * (t - rng.integers(0, 7)) / 7)
        weekend = np.where(dates.dayofweek >= 5, rng.uniform(0.9, 1.4), 1.0)
        yearly = 1 + rng.uniform(0.05, 0.3) * np.sin(2 * np.pi * (t - rng.integers(0, 365)) / 365.25)
        promo = (rng.random(n_days) < 0.08).astype(int)
        uplift = 1 + promo * rng.uniform(0.3, 0.9)
        mean = np.maximum(base * (1 + trend * t / 100) * weekly * weekend * yearly * uplift, 1.0)
        y = rng.poisson(mean).astype(float)
        frames.append(pd.DataFrame({"series_id": f"S{s:02d}", "date": dates,
                                    "promo": promo, "y": y}))
    return pd.concat(frames, ignore_index=True)
