import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flagsvc.api import create_app
from flagsvc.models import FlagDefinition
from flagsvc.store import FlagStore, VersionConflict

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    return TestClient(create_app(FlagStore()))


def test_store_versions_and_history():
    s = FlagStore()
    assert s.put("a", FlagDefinition()) == 1
    assert s.put("a", FlagDefinition(enabled=False)) == 2
    assert s.get("a")[1] == 2
    assert [h["action"] for h in s.history("a")] == ["create", "update"]
    assert s.delete("a") and not s.delete("a")
    assert s.get("a") is None
    assert s.history("a")[-1]["action"] == "delete"


def test_store_optimistic_concurrency():
    s = FlagStore()
    s.put("a", FlagDefinition(), expected_version=0)
    with pytest.raises(VersionConflict):
        s.put("a", FlagDefinition(), expected_version=0)
    s.put("a", FlagDefinition(), expected_version=1)


def test_store_persists_to_file(tmp_path):
    p = str(tmp_path / "f.db")
    FlagStore(p).put("a", FlagDefinition(default_value="x"))
    assert FlagStore(p).get("a")[0].default_value == "x"


def test_crud_and_evaluate(client):
    body = json.loads((ROOT / "samples/flags.json").read_text())["bulk-export"]
    assert client.put("/flags/bulk-export", json=body).json() == {"key": "bulk-export", "version": 1}
    assert client.get("/flags/bulk-export").json()["version"] == 1
    r = client.post("/evaluate/bulk-export", json={"plan": "pro", "seats": 20})
    assert r.json() == {"key": "bulk-export", "value": True, "reason": "rule_match", "rule_index": 0}
    assert client.post("/evaluate/bulk-export", json={"plan": "pro", "seats": 2}).json()["value"] is False
    assert client.delete("/flags/bulk-export").status_code == 204
    assert client.get("/flags/bulk-export").status_code == 404


def test_missing_flag_fails_closed(client):
    r = client.post("/evaluate/nope", json={})
    assert r.status_code == 200 and r.json()["reason"] == "missing_flag" and r.json()["value"] is False


def test_if_match_conflict_returns_409(client):
    client.put("/flags/a", json={})
    assert client.put("/flags/a", json={}, headers={"If-Match": "5"}).status_code == 409
    assert client.put("/flags/a", json={}, headers={"If-Match": "1"}).json()["version"] == 2


def test_validation_errors(client):
    assert client.put("/flags/a", json={"rules": [{}]}).status_code == 422
    assert client.put("/flags/Bad_Key", json={}).status_code == 422


def test_bulk_evaluate(client):
    client.put("/flags/a", json={"default_value": True})
    client.put("/flags/b", json={"enabled": False, "off_value": "off"})
    r = client.post("/evaluate", json={"context": {"user_id": "u"}}).json()
    assert r["a"]["value"] is True and r["b"]["reason"] == "disabled"
    r = client.post("/evaluate", json={"keys": ["a", "zzz"]}).json()
    assert r["zzz"]["reason"] == "missing_flag"


def test_history_endpoint(client):
    client.put("/flags/a", json={})
    client.put("/flags/a", json={"enabled": False})
    h = client.get("/flags/a/history").json()
    assert [x["version"] for x in h] == [1, 2]
    assert client.get("/flags/zz/history").status_code == 404


def test_sample_output_matches_expected():
    import subprocess, sys
    r = subprocess.run([sys.executable, str(ROOT / "scripts/run_sample.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
