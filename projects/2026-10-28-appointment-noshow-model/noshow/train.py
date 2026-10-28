"""Train/evaluate no-show models with a temporal split."""
import argparse
import json
import sys

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .data import generate
from .features import NUMERIC, TARGET, add_features


def temporal_split(df: pd.DataFrame, test_frac: float = 0.25):
    """Split by date: the test set is strictly later than the train set."""
    cutoff = df["date"].sort_values().iloc[int(len(df) * (1 - test_frac))]
    return df[df["date"] < cutoff], df[df["date"] >= cutoff]


def evaluate(y, p) -> dict:
    return {"roc_auc": round(float(roc_auc_score(y, p)), 4),
            "pr_auc": round(float(average_precision_score(y, p)), 4),
            "brier": round(float(brier_score_loss(y, p)), 4)}


def run(df: pd.DataFrame, seed: int = 0) -> dict:
    train_raw, test_raw = temporal_split(df)
    # global smoothing rate from TRAIN only; history features are causal (see tests).
    rate = float(train_raw[TARGET].mean())
    full = add_features(df, global_rate=rate)
    cutoff = test_raw["date"].min()
    train, test = full[full["date"] < cutoff], full[full["date"] >= cutoff]
    Xtr, ytr, Xte, yte = train[NUMERIC], train[TARGET], test[NUMERIC], test[TARGET]

    models = {
        "baseline_train_prior": DummyClassifier(strategy="prior"),
        "logistic_regression": make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
        "hist_gradient_boosting": HistGradientBoostingClassifier(
            max_depth=3, learning_rate=0.05, max_iter=200, random_state=seed),
    }
    results = {"n_train": len(train), "n_test": len(test),
               "train_noshow_rate": round(rate, 4),
               "test_noshow_rate": round(float(yte.mean()), 4), "models": {}}
    for name, m in models.items():
        m.fit(Xtr, ytr)
        results["models"][name] = evaluate(yte, m.predict_proba(Xte)[:, 1])
    return results


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="CSV with patient_id,date,age,lead_days,hour,sms_reminder,no_show")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=None, help="write metrics JSON here")
    a = ap.parse_args(argv)
    if a.input:
        df = pd.read_csv(a.input, parse_dates=["date"])
    else:
        df = generate(seed=a.seed)
    res = run(df, seed=a.seed)
    text = json.dumps(res, indent=2)
    print(text)
    if a.out:
        with open(a.out, "w") as f:
            f.write(text + "\n")
    return res


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
