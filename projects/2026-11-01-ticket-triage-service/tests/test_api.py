import pytest
from fastapi.testclient import TestClient

from triage import etl, generate, model
from triage.api import create_app


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    d = tmp_path_factory.mktemp("art")
    df, _ = etl.clean(generate.generate_tickets(800, seed=5))
    model.train_final(df, d / "m.joblib")
    with TestClient(create_app(str(d / "m.joblib"), str(d / "p.db"))) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and "billing" in r.json()["classes"]


def test_predict(client):
    r = client.post("/predict", json={"text": "I was charged twice for my pro subscription"})
    assert r.status_code == 200
    assert r.json()["label"] == "billing"
    assert 0 < r.json()["confidence"] <= 1


def test_batch_and_stats(client):
    r = client.post("/predict/batch", json={"texts": ["The app crashes on the dashboard page", "My package never arrived"]})
    labels = [p["label"] for p in r.json()["predictions"]]
    assert labels == ["bug", "shipping"]
    stats = client.get("/stats").json()["by_label"]
    assert stats["bug"]["count"] >= 1


@pytest.mark.parametrize("payload", [{"text": ""}, {"text": "   "}, {}, {"text": "x" * 6000}])
def test_predict_rejects_bad_input(client, payload):
    assert client.post("/predict", json=payload).status_code == 422


def test_missing_model_fails(tmp_path):
    with pytest.raises(RuntimeError):
        with TestClient(create_app(str(tmp_path / "nope.joblib"), str(tmp_path / "x.db"))):
            pass
