from fastapi import FastAPI
from fastapi.testclient import TestClient

from ratelimiter import RateLimitMiddleware
from ratelimiter.middleware import rule


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def build(clock):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, rules=[
        rule("/", "fixed_window", 100, 60, clock),
        rule("/login", "sliding_log", 2, 60, clock),
    ])

    @app.get("/health")
    def health(): return {"ok": True}

    @app.get("/login")
    def login(): return {"ok": True}

    @app.get("/other")
    def other(): return {"ok": True}

    return app


def test_429_with_headers_and_recovery():
    clock = Clock()
    c = TestClient(build(clock))
    r = c.get("/login")
    assert r.status_code == 200 and r.headers["x-ratelimit-remaining"] == "1"
    c.get("/login")
    r = c.get("/login")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60"
    assert r.json() == {"detail": "rate limit exceeded", "retry_after": 60}
    clock.t = 61
    assert c.get("/login").status_code == 200


def test_longest_prefix_and_per_rule_quota():
    c = TestClient(build(Clock()))
    for _ in range(3):
        c.get("/login")
    assert c.get("/other").status_code == 200  # separate rule/quota


def test_clients_isolated_by_api_key():
    c = TestClient(build(Clock()))
    for _ in range(3):
        c.get("/login", headers={"x-api-key": "A"})
    assert c.get("/login", headers={"x-api-key": "A"}).status_code == 429
    assert c.get("/login", headers={"x-api-key": "B"}).status_code == 200


def test_exempt_path_has_no_limit_headers():
    r = TestClient(build(Clock())).get("/health")
    assert r.status_code == 200 and "x-ratelimit-limit" not in r.headers
