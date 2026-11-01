"""Training and evaluation: majority baseline vs TF-IDF + logistic regression."""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import GroupShuffleSplit, train_test_split
from sklearn.pipeline import Pipeline

SEED = 42


def split(df: pd.DataFrame, mode: str = "random", test_size: float = 0.25, seed: int = SEED):
    """Reproducible split.

    random: stratified by label.
    group:  whole ``template_id`` groups are held out, so test phrasing is unseen.
    """
    if mode == "random":
        return train_test_split(df, test_size=test_size, random_state=seed, stratify=df["label"])
    if mode == "group":
        gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        tr, te = next(gss.split(df, groups=df["template_id"]))
        return df.iloc[tr], df.iloc[te]
    raise ValueError(f"unknown split mode: {mode}")


def build_pipeline() -> Pipeline:
    # The vectorizer lives inside the pipeline so it is fit on training data only.
    return Pipeline([
        ("tfidf", TfidfVectorizer(lowercase=True, ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("clf", LogisticRegression(max_iter=1000, C=5.0, random_state=SEED)),
    ])


def _metrics(y_true, y_pred) -> dict:
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro")), 4),
    }


def evaluate(df: pd.DataFrame, mode: str = "random") -> dict:
    train, test = split(df, mode)
    base = DummyClassifier(strategy="most_frequent").fit(train["text"], train["label"])
    model = build_pipeline().fit(train["text"], train["label"])
    return {
        "split": mode,
        "n_train": len(train),
        "n_test": len(test),
        "baseline_majority": _metrics(test["label"], base.predict(test["text"])),
        "tfidf_logreg": _metrics(test["label"], model.predict(test["text"])),
    }


def train_final(df: pd.DataFrame, path: str | Path) -> Pipeline:
    """Fit on all data and persist for serving (after evaluation)."""
    model = build_pipeline().fit(df["text"], df["label"])
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    return model


def predict(model: Pipeline, texts: list[str]) -> list[dict]:
    proba = model.predict_proba(texts)
    classes = model.classes_
    out = []
    for row in proba:
        i = int(np.argmax(row))
        out.append({"label": str(classes[i]), "confidence": round(float(row[i]), 4)})
    return out


def save_report(report: dict, path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(report, indent=2))
