import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimitkit import (FixedWindow, Limiter, MemoryStore, RateLimitMiddleware, Rule,
                          TokenBucket)
from ratelimitkit.cli import simulate


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def build(rules, **kw):
    app = FastAPI()

    @app.get("/search")
    def search():
        return {"ok": True}

    @app.get("/other")
    def other():
        return {"ok": True}

    @app.post("/search")
    def post_search():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, rules=rules, **kw)
    return TestClient(app, raise_server_exceptions=False)


def test_429_with_headers_and_recovery():
    clock = Clock()
    c = build([Rule("/search", Limiter(FixedWindow(2, 10), clock=clock))])
    r1 = c.get("/search")
    assert r1.status_code == 200
    assert r1.headers["ratelimit-limit"] == "2"
    assert r1.headers["ratelimit-remaining"] == "1"
    assert c.get("/search").status_code == 200
    r3 = c.get("/search")
    assert r3.status_code == 429
    assert r3.json()["detail"] == "rate limit exceeded"
    assert r3.headers["retry-after"] == "10"  # clock sits on a window boundary
    assert r3.headers["ratelimit-remaining"] == "0"
    clock.t += 10
    assert c.get("/search").status_code == 200


def test_unmatched_paths_not_limited():
    c = build([Rule("/search", Limiter(FixedWindow(1, 10), clock=Clock()))])
    for _ in range(5):
        r = c.get("/other")
        assert r.status_code == 200 and "ratelimit-limit" not in r.headers


def test_method_filter():
    rule = Rule("/search", Limiter(FixedWindow(1, 10), clock=Clock()), methods=frozenset({"POST"}))
    c = build([rule])
    assert all(c.get("/search").status_code == 200 for _ in range(3))
    assert c.post("/search").status_code == 200
    assert c.post("/search").status_code == 429


def test_first_matching_rule_wins():
    strict = Rule("/search", Limiter(FixedWindow(1, 10), clock=Clock(), name="s"))
    loose = Rule("/", Limiter(FixedWindow(100, 10), clock=Clock(), name="l"))
    c = build([strict, loose])
    assert c.get("/search").status_code == 200
    assert c.get("/search").status_code == 429
    assert c.get("/other").status_code == 200


def test_api_keys_have_separate_buckets_and_share_across_ips():
    c = build([Rule("/", Limiter(TokenBucket(1, 0.001), clock=Clock()))])
    assert c.get("/search", headers={"x-api-key": "A"}).status_code == 200
    assert c.get("/search", headers={"x-api-key": "A"}).status_code == 429
    assert c.get("/search", headers={"x-api-key": "B"}).status_code == 200
    assert c.get("/search").status_code == 200  # anonymous client (by IP)


class BrokenStore:
    def update(self, *a, **k):
        raise ConnectionError("store down")


def test_fail_open_and_fail_closed():
    rules = [Rule("/", Limiter(FixedWindow(1, 10), BrokenStore(), Clock()))]
    assert build(rules, fail_open=True).get("/search").status_code == 200
    closed = build(rules, fail_open=False)
    assert closed.get("/search").status_code == 500


def test_cli_matches_golden_sample():
    root = Path(__file__).resolve().parent.parent / "samples"
    scenario = json.loads((root / "scenario.json").read_text())
    expected = json.loads((root / "expected_output.json").read_text())
    assert simulate(scenario) == expected


def test_cli_known_semantics():
    out = simulate(json.loads((Path(__file__).resolve().parent.parent / "samples" / "scenario.json").read_text()))
    # hand-derived: key a plus b's cost-2 request, see README
    assert out["fixed_window"]["allowed"] == 8
    assert out["sliding_log"]["allowed"] == 8
    assert out["sliding_counter"]["allowed"] == 6
    assert out["token_bucket"]["allowed"] == 9
