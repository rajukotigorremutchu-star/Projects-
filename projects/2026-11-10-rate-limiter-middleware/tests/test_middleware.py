import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimit import ManualClock, RateLimitMiddleware, Rule, make_limiter


def build(clock, key_func=None, limiter=None):
    app = FastAPI()
    kw = {"key_func": key_func} if key_func else {}
    app.add_middleware(RateLimitMiddleware, rules=[
        Rule("/limited", limiter or make_limiter("sliding_log", 2, 10, clock)),
        Rule("/costly", make_limiter("token_bucket", 10, 10, clock), cost=5),
    ], **kw)

    @app.get("/limited")
    def limited(): return {"ok": 1}

    @app.get("/costly")
    def costly(): return {"ok": 1}

    @app.get("/free")
    def free(): return {"ok": 1}

    @app.get("/health")
    def health(): return {"ok": 1}

    return app


def test_headers_on_success_and_429_with_retry_after():
    c = ManualClock(0)
    cl = TestClient(build(c))
    r = cl.get("/limited")
    assert r.status_code == 200
    assert r.headers["ratelimit-limit"] == "2" and r.headers["ratelimit-remaining"] == "1"
    cl.get("/limited")
    r = cl.get("/limited")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "10"
    assert r.json()["detail"] == "rate limit exceeded"
    assert r.headers["ratelimit-remaining"] == "0"


def test_recovers_after_retry_after():
    c = ManualClock(0)
    cl = TestClient(build(c))
    for _ in range(3):
        cl.get("/limited")
    c.advance(10)
    assert cl.get("/limited").status_code == 200


def test_unmatched_and_exempt_paths_unlimited():
    cl = TestClient(build(ManualClock(0)))
    assert all(cl.get("/free").status_code == 200 for _ in range(20))
    assert all(cl.get("/health").status_code == 200 for _ in range(20))


def test_cost_per_rule():
    cl = TestClient(build(ManualClock(0)))
    assert [cl.get("/costly").status_code for _ in range(3)] == [200, 200, 429]


def test_custom_key_func_separates_clients():
    cl = TestClient(build(ManualClock(0), key_func=lambda s: dict(s["headers"]).get(b"x-api-key", b"anon").decode()))
    for _ in range(2):
        assert cl.get("/limited", headers={"x-api-key": "a"}).status_code == 200
    assert cl.get("/limited", headers={"x-api-key": "a"}).status_code == 429
    assert cl.get("/limited", headers={"x-api-key": "b"}).status_code == 200


def test_fails_open_when_limiter_errors():
    class Broken:
        def check(self, *a, **k): raise RuntimeError("store down")

    cl = TestClient(build(ManualClock(0), limiter=Broken()))
    assert cl.get("/limited").status_code == 200


def test_impossible_cost_has_no_retry_after():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware,
                       rules=[Rule("/", make_limiter("token_bucket", 3, 10, ManualClock(0)), cost=9)])

    @app.get("/x")
    def x(): return 1

    r = TestClient(app).get("/x")
    assert r.status_code == 429 and "retry-after" not in r.headers


def test_demo_app_search_limit():
    from app import app
    cl = TestClient(app)
    codes = [cl.get("/search?q=a").status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
