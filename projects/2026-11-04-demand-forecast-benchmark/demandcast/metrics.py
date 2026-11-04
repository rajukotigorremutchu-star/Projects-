from __future__ import annotations

import numpy as np


def mae(y, p) -> float:
    return float(np.mean(np.abs(np.asarray(y) - np.asarray(p))))


def wape(y, p) -> float:
    """Weighted absolute percentage error: sum|e| / sum|y|."""
    y = np.asarray(y, float)
    denom = np.abs(y).sum()
    if denom == 0:
        raise ValueError("WAPE undefined when actuals sum to zero")
    return float(np.abs(y - np.asarray(p)).sum() / denom)


def mase_scale(train_y: np.ndarray, season: int = 7) -> float:
    """In-sample MAE of the seasonal-naive forecast (denominator of MASE)."""
    train_y = np.asarray(train_y, float)
    if len(train_y) <= season:
        raise ValueError("training series shorter than season")
    scale = np.mean(np.abs(train_y[season:] - train_y[:-season]))
    if scale == 0:
        raise ValueError("MASE scale is zero")
    return float(scale)
