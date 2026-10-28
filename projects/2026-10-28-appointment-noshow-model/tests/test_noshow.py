import pandas as pd
import pytest

from noshow.data import generate
from noshow.features import TARGET, add_features
from noshow.train import run, temporal_split


@pytest.fixture(scope="module")
def df():
    return generate(n_patients=300, seed=1)


def test_generate_deterministic():
    pd.testing.assert_frame_equal(generate(50, seed=3), generate(50, seed=3))


def test_temporal_split_no_overlap(df):
    tr, te = temporal_split(df)
    assert tr["date"].max() < te["date"].min()
    assert len(tr) + len(te) == len(df)


def test_history_features_exclude_own_label():
    d = pd.DataFrame({"patient_id": [1, 1, 1],
                      "date": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03"]),
                      "age": 30, "lead_days": 1, "hour": 9, "sms_reminder": 0,
                      "no_show": [1, 0, 1]})
    f = add_features(d, prior_strength=2.0, global_rate=0.2)
    assert list(f["prior_appts"]) == [0, 1, 2]
    assert f["prior_noshow_rate"].iloc[0] == pytest.approx(0.2)           # no history -> prior
    assert f["prior_noshow_rate"].iloc[1] == pytest.approx((1 + 0.4) / 3)  # only row 0 seen
    assert f["prior_noshow_rate"].iloc[2] == pytest.approx((1 + 0.4) / 4)


def test_features_unchanged_by_future_labels(df):
    """Flipping labels of the last half must not alter features of the first half."""
    a = add_features(df, global_rate=0.2)
    b = df.copy()
    half = b["date"].sort_values().iloc[len(b) // 2]
    b.loc[b["date"] >= half, TARGET] = 1 - b.loc[b["date"] >= half, TARGET]
    b = add_features(b, global_rate=0.2)
    mask = a["date"] < half
    pd.testing.assert_series_equal(a.loc[mask, "prior_noshow_rate"], b.loc[mask, "prior_noshow_rate"])


def test_run_reproducible_and_beats_baseline(df):
    r1, r2 = run(df, seed=0), run(df, seed=0)
    assert r1 == r2
    m = r1["models"]
    assert m["baseline_train_prior"]["roc_auc"] == pytest.approx(0.5)
    assert m["logistic_regression"]["roc_auc"] > 0.55
    assert m["logistic_regression"]["brier"] <= m["baseline_train_prior"]["brier"]
