"""Feature construction for direct h-step-ahead forecasting.

A row describes "the forecast for target day t, issued at origin t-h".
Every feature derived from y uses values at or before t-h, i.e. lags >= h.
`promo` and calendar fields are known in advance and may refer to day t.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HORIZON = 7
LAGS = (7, 14, 21, 28)
ROLL_WINDOWS = (7, 28)


def build_features(df: pd.DataFrame, horizon: int = HORIZON) -> pd.DataFrame:
    df = df.sort_values(["series_id", "date"]).reset_index(drop=True).copy()
    g = df.groupby("series_id")["y"]
    for lag in LAGS:
        if lag < horizon:
            raise ValueError(f"lag {lag} < horizon {horizon} would leak the future")
        df[f"lag_{lag}"] = g.shift(lag)
    # Rolling stats end at the forecast origin (t-h): shift(h) first, then roll.
    for w in ROLL_WINDOWS:
        df[f"roll_mean_{w}"] = g.transform(lambda s: s.shift(horizon).rolling(w).mean())
    df["dow"] = df["date"].dt.dayofweek
    df["is_weekend"] = (df["dow"] >= 5).astype(int)
    df["doy_sin"] = np.sin(2 * np.pi * df["date"].dt.day_of_year / 365.25)
    df["doy_cos"] = np.cos(2 * np.pi * df["date"].dt.day_of_year / 365.25)
    df["series_code"] = df["series_id"].astype("category").cat.codes
    return df


FEATURE_COLS = [f"lag_{l}" for l in LAGS] + [f"roll_mean_{w}" for w in ROLL_WINDOWS] + [
    "dow", "is_weekend", "doy_sin", "doy_cos", "promo", "series_code"]
