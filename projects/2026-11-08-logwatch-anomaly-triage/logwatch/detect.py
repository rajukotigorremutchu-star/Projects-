"""Two detectors: a robust seasonal z-score baseline and an IsolationForest."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

METRICS = ("requests", "error_rate", "p95_ms")
Z_THRESHOLD = 6.0  # fixed a priori, not tuned on test data


def _hour(minute: pd.Series) -> pd.Series:
    return pd.to_datetime(minute).dt.hour


class RobustZBaseline:
    """Per (service, hour-of-day) median/MAD of each metric, fitted on training data only.

    Score = max absolute robust z over metrics. Flag when score > threshold.
    """

    def __init__(self, threshold: float = Z_THRESHOLD):
        self.threshold = threshold

    def fit(self, df: pd.DataFrame) -> "RobustZBaseline":
        d = df.assign(hour=_hour(df["minute"]))
        self.stats_ = {}
        for (svc, hr), g in d.groupby(["service", "hour"]):
            for m in METRICS:
                med = float(g[m].median())
                mad = float((g[m] - med).abs().median())
                self.stats_[(svc, int(hr), m)] = (med, mad)
        # per-metric scale floor so near-constant metrics (error_rate ~ 0.01) don't explode
        p0 = max(float(df["error_rate"].mean()), 1e-3)  # pooled training error rate
        self.floor_ = {m: max(0.05 * float(df[m].median()), 1e-3) for m in METRICS}
        # binomial sd of an error rate measured over ~median requests/min
        self.floor_["error_rate"] = float(np.sqrt(p0 * (1 - p0) / max(df["requests"].median(), 1)))
        return self

    def z_scores(self, service: str, hour: int, values: dict[str, float]) -> dict[str, float]:
        out = {}
        for m in METRICS:
            med, mad = self.stats_[(service, hour, m)]
            scale = max(1.4826 * mad, self.floor_[m], np.sqrt(med) if m == "requests" else 0.0)  # Poisson-like floor for counts
            out[m] = (values[m] - med) / scale
        return out

    def score(self, df: pd.DataFrame) -> np.ndarray:
        hrs = _hour(df["minute"]).to_numpy()
        res = np.empty(len(df))
        for i, (svc, hr, row) in enumerate(zip(df["service"], hrs, df[list(METRICS)].to_dict("records"))):
            res[i] = max(abs(v) for v in self.z_scores(svc, int(hr), row).values())
        return res

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.score(df) > self.threshold


class IForestDetector:
    """IsolationForest on log-scaled metrics + time-of-day + service one-hot.

    Unsupervised: the flag threshold is the (1 - contamination) quantile of
    *training* scores, so no test data or labels influence it.
    """

    def __init__(self, contamination: float = 0.01, seed: int = 0, n_estimators: int = 200):
        self.contamination, self.seed, self.n_estimators = contamination, seed, n_estimators

    def _x(self, df: pd.DataFrame) -> np.ndarray:
        t = pd.to_datetime(df["minute"])
        frac = (t.dt.hour + t.dt.minute / 60.0).to_numpy() / 24.0
        cols = [np.log1p(df["requests"].to_numpy()), df["error_rate"].to_numpy() * 10,
                np.log1p(df["p95_ms"].to_numpy()), np.sin(2 * np.pi * frac), np.cos(2 * np.pi * frac)]
        cols += [(df["service"] == s).to_numpy().astype(float) for s in self.services_]
        return np.column_stack(cols)

    def fit(self, df: pd.DataFrame) -> "IForestDetector":
        self.services_ = sorted(df["service"].unique())
        self.model_ = IsolationForest(n_estimators=self.n_estimators, random_state=self.seed)
        self.model_.fit(self._x(df))
        self.threshold_ = float(np.quantile(self.score(df), 1 - self.contamination))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        return -self.model_.score_samples(self._x(df))  # higher = more anomalous

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return self.score(df) > self.threshold_
