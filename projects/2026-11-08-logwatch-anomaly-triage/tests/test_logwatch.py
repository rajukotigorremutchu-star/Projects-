import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from logwatch.api import create_app
from logwatch.etl import build_features
from logwatch.evaluate import incident_recall, prf, time_split
from logwatch.generate import generate
from logwatch.triage import likely_kind

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def test_generator_is_deterministic():
    a, la, _ = generate(1, 3)
    b, lb, _ = generate(1, 3)
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(la, lb)


def test_etl_fills_gaps_and_counts_correctly():
    logs = pd.DataFrame({
        "ts": pd.to_datetime(["2026-01-01 00:00:10", "2026-01-01 00:00:20", "2026-01-01 00:03:05"]),
        "service": "a", "status": [200, 500, 200], "latency_ms": [10.0, 30.0, 5.0]})
    f = build_features(logs)
    assert list(f["requests"]) == [2, 0, 0, 1]  # minutes 01 and 02 are empty but present
    assert f.loc[0, "error_rate"] == pytest.approx(0.5)
    assert f.loc[0, "p95_ms"] == pytest.approx(29.0)  # linear interpolation of 10, 30
    assert f.loc[1, "p95_ms"] == 0.0


def test_etl_rejects_empty():
    with pytest.raises(ValueError):
        build_features(pd.DataFrame({"ts": [], "service": [], "status": [], "latency_ms": []}))


def test_time_split_is_chronological_without_overlap(result):
    assert result.train["minute"].max() < result.test["minute"].min()
    assert len(result.train) + len(result.test) == 3 * 6 * 1440


def test_metric_helpers():
    y = pd.Series([True, True, False, False]).to_numpy()
    p = pd.Series([True, False, True, False]).to_numpy()
    m = prf(y, p)
    assert (m["tp"], m["fp"], m["fn"]) == (1, 1, 1) and m["f1"] == 0.5
    df = pd.DataFrame({"service": "a", "minute": pd.date_range("2026-01-01", periods=6, freq="min"),
                       "is_incident": [True, True, False, False, True, True],
                       "incident_type": ["x", "x", "", "", "x", "x"]})
    r = incident_recall(df, [False, True, False, False, False, False])
    assert (r["incidents"], r["detected"]) == (2, 1)


def test_baseline_beats_chance_and_meets_floor(result):
    m = result.metrics["robust_z_baseline"]
    assert m["f1"] > 0.7 and m["incident_level"]["recall"] >= 0.9


def test_iforest_runs_and_is_precise(result):
    m = result.metrics["isolation_forest"]
    assert m["precision"] > 0.5 and m["tp"] > 0


def test_metrics_match_committed_expected_output(result):
    expected = json.loads((SAMPLES / "expected_metrics.json").read_text())
    assert result.metrics == expected


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(days=6, seed=7))


def test_api_flags_error_spike_with_reason(client):
    body = json.loads((SAMPLES / "score_request_anomaly.json").read_text())
    r = client.post("/score", json=body).json()
    assert r["anomaly"] is True and r["likely_kind"] == "error_spike"
    assert any("error rate" in x for x in r["reasons"])


def test_api_accepts_normal_window(client):
    body = json.loads((SAMPLES / "score_request_normal.json").read_text())
    r = client.post("/score", json=body).json()
    assert r["anomaly"] is False and r["reasons"] == []


def test_api_validation(client):
    bad = {"service": "nope", "minute": "2026-01-10T12:00:00", "requests": 10, "error_rate": 0.0, "p95_ms": 50}
    assert client.post("/score", json=bad).status_code == 422
    bad.update(service="auth", error_rate=2)
    assert client.post("/score", json=bad).status_code == 422
    assert client.get("/health").json()["status"] == "ok"
    assert "robust_z_baseline" in client.get("/metrics").json()


def test_likely_kind():
    assert likely_kind([]) == "unknown"
    assert likely_kind(["request volume 1 is below typical 30"]) == "traffic_drop"
