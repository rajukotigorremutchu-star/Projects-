"""Chronological split and metrics (minute-level and incident-level)."""
from __future__ import annotations

import pandas as pd


def time_split(feats: pd.DataFrame, train_fraction: float = 2 / 3):
    """Train on the earliest fraction of the timeline, test on the rest. No shuffling."""
    cut = feats["minute"].min() + (feats["minute"].max() - feats["minute"].min()) * train_fraction
    cut = cut.floor("D")
    return feats[feats["minute"] < cut].reset_index(drop=True), feats[feats["minute"] >= cut].reset_index(drop=True)


def prf(y_true, y_pred) -> dict:
    tp = int((y_true & y_pred).sum())
    fp = int((~y_true & y_pred).sum())
    fn = int((y_true & ~y_pred).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4), "tp": tp, "fp": fp, "fn": fn}


def incident_recall(df: pd.DataFrame, flags) -> dict:
    """An incident counts as detected if any of its minutes is flagged."""
    d = df.assign(flag=flags)
    d = d[d["is_incident"]]
    if d.empty:
        return {"incidents": 0, "detected": 0, "recall": 0.0}
    # contiguous runs of the same (service, incident_type) are one incident
    d = d.sort_values(["service", "minute"])
    run = ((d["service"] != d["service"].shift()) | (d["incident_type"] != d["incident_type"].shift())
           | (d["minute"].diff() != pd.Timedelta(minutes=1))).cumsum()
    per = d.groupby(run)["flag"].any()
    return {"incidents": int(len(per)), "detected": int(per.sum()), "recall": round(float(per.mean()), 4)}


def evaluate(test: pd.DataFrame, flags) -> dict:
    return {**prf(test["is_incident"].to_numpy(), flags), "incident_level": incident_recall(test, flags)}
