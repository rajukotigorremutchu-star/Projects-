import numpy as np
import pandas as pd
import pytest

from demandcast.benchmark import run_benchmark, temporal_split
from demandcast.data import generate_demand
from demandcast.features import FEATURE_COLS, HORIZON, build_features
from demandcast.metrics import mae, mase_scale, wape


@pytest.fixture(scope="module")
def raw():
    return generate_demand(n_series=4, n_days=300, seed=1)


def test_generator_deterministic_and_valid(raw):
    again = generate_demand(n_series=4, n_days=300, seed=1)
    pd.testing.assert_frame_equal(raw, again)
    assert (raw["y"] >= 0).all() and set(raw["promo"]) <= {0, 1}
    assert raw.groupby("series_id").size().eq(300).all()


def test_no_future_leakage_in_features(raw):
    """Changing y at/after the forecast origin+1 must not alter row t's features."""
    base = build_features(raw)
    t = pd.Timestamp("2024-09-01")
    mod = raw.copy()
    # Origin for target t is t-HORIZON; corrupt everything after the origin.
    mod.loc[mod["date"] > t - pd.Timedelta(days=HORIZON), "y"] = 1e9
    new = build_features(mod)
    cols = [c for c in FEATURE_COLS if c.startswith(("lag_", "roll_"))]
    a = base[base["date"] == t][cols].to_numpy()
    b = new[new["date"] == t][cols].to_numpy()
    np.testing.assert_array_equal(a, b)


def test_short_lag_rejected(raw):
    with pytest.raises(ValueError):
        build_features(raw, horizon=14)  # lag_7 < 14


def test_temporal_split_disjoint_and_ordered(raw):
    f = build_features(raw)
    tr, va, te = temporal_split(f, 28, 56)
    assert tr["date"].max() < va["date"].min() <= va["date"].max() < te["date"].min()
    assert te["date"].nunique() == 56 and va["date"].nunique() == 28
    assert len(tr) + len(va) + len(te) == len(f)


def test_metrics():
    assert mae([1, 2, 3], [2, 2, 5]) == pytest.approx(1.0)
    assert wape([10, 10], [8, 13]) == pytest.approx(5 / 20)
    with pytest.raises(ValueError):
        wape([0, 0], [1, 1])
    # Perfect weekly repetition -> zero scale -> error; otherwise positive.
    with pytest.raises(ValueError):
        mase_scale(np.tile(np.arange(7.0), 3))
    assert mase_scale(np.arange(20.0)) == pytest.approx(7.0)


def test_benchmark_reproducible_and_sane(raw):
    r1 = run_benchmark(raw, seed=3)
    r2 = run_benchmark(raw, seed=3)
    assert r1 == r2
    assert set(r1["test"]) == {"seasonal_naive_7", "moving_avg_28", "ridge", "hist_gbm"}
    assert r1["config"]["train_end"] < r1["config"]["test_start"]
    for s in r1["test"].values():
        assert np.isfinite(list(s.values())).all()
    # Learned models should be competitive with the naive baseline on this data.
    best = min(r1["test"]["ridge"]["wape"], r1["test"]["hist_gbm"]["wape"])
    assert best < r1["test"]["seasonal_naive_7"]["wape"]
