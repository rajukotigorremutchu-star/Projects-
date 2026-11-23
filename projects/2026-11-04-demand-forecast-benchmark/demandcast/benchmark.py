"""Temporal split, baselines, model, and evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_COLS, HORIZON, build_features
from .metrics import mae, mase_scale, wape


def temporal_split(df: pd.DataFrame, val_days: int = 28, test_days: int = 56):
    """Split by date (never random). Returns train, val, test masks' frames."""
    last = df["date"].max()
    test_start = last - pd.Timedelta(days=test_days - 1)
    val_start = test_start - pd.Timedelta(days=val_days)
    train = df[df["date"] < val_start]
    val = df[(df["date"] >= val_start) & (df["date"] < test_start)]
    test = df[df["date"] >= test_start]
    return train, val, test


def _models(seed: int):
    return {
        "ridge": lambda: make_pipeline(StandardScaler(), Ridge(alpha=10.0)),
        "hist_gbm": lambda: HistGradientBoostingRegressor(
            loss="poisson", max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
            random_state=seed),
    }


def run_benchmark(raw: pd.DataFrame, seed: int = 0, val_days: int = 28,
                  test_days: int = 56) -> dict:
    feats = build_features(raw, HORIZON).dropna(subset=FEATURE_COLS).reset_index(drop=True)
    train, val, test = temporal_split(feats, val_days, test_days)
    scale = float(np.mean([
        mase_scale(g["y"].to_numpy()) for _, g in raw[raw["date"] <= train["date"].max()]
        .groupby("series_id")]))

    def score(y, p):
        return {"mae": mae(y, p), "wape": wape(y, p), "mase": mae(y, p) / scale}

    preds = {
        "seasonal_naive_7": test["lag_7"].to_numpy(),
        "moving_avg_28": test["roll_mean_28"].to_numpy(),
    }
    val_scores = {}
    for name, make in _models(seed).items():
        m = make().fit(train[FEATURE_COLS], train["y"])
        val_scores[name] = wape(val["y"], np.maximum(m.predict(val[FEATURE_COLS]), 0))
        # Refit on train+val for the final test forecast (no test rows used).
        trainval = pd.concat([train, val])
        m = make().fit(trainval[FEATURE_COLS], trainval["y"])
        preds[name] = np.maximum(m.predict(test[FEATURE_COLS]), 0)

    results = {name: score(test["y"], p) for name, p in preds.items()}
    return {
        "config": {"seed": seed, "horizon": HORIZON, "val_days": val_days,
                   "test_days": test_days, "n_train": len(train), "n_val": len(val),
                   "n_test": len(test), "train_end": str(train["date"].max().date()),
                   "test_start": str(test["date"].min().date())},
        "validation_wape": val_scores,
        "test": results,
    }


def format_report(res: dict) -> str:
    lines = ["model,mae,wape,mase"]
    for name, s in res["test"].items():
        lines.append(f"{name},{s['mae']:.3f},{s['wape']:.4f},{s['mase']:.4f}")
    return "\n".join(lines)
