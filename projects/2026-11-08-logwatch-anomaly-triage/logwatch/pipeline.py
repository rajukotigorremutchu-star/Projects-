"""End-to-end: generate -> ETL -> split -> fit -> evaluate."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .detect import IForestDetector, RobustZBaseline
from .etl import build_features
from .evaluate import evaluate, time_split
from .generate import generate


@dataclass
class Result:
    metrics: dict
    baseline: RobustZBaseline
    iforest: IForestDetector
    train: pd.DataFrame
    test: pd.DataFrame


def run(days: int = 6, seed: int = 7) -> Result:
    logs, labels, _ = generate(days, seed)
    feats = build_features(logs).merge(labels, on=["service", "minute"], how="left")
    train, test = time_split(feats)
    base = RobustZBaseline().fit(train)  # labels never used for fitting
    iso = IForestDetector(seed=seed).fit(train)
    metrics = {
        "config": {"days": days, "seed": seed, "raw_log_rows": int(len(logs)),
                   "train_minutes": int(len(train)), "test_minutes": int(len(test)),
                   "test_incident_minutes": int(test["is_incident"].sum())},
        "robust_z_baseline": evaluate(test, base.predict(test)),
        "isolation_forest": evaluate(test, iso.predict(test)),
    }
    return Result(metrics, base, iso, train, test)
