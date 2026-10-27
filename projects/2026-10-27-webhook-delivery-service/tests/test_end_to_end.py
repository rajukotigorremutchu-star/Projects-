"""Dispatcher -> real HttpxTransport -> in-process signed receiver (no sockets)."""
import httpx
from fastapi.testclient import TestClient

from webhooks.app import create_app
from webhooks.dispatcher import HttpxTransport
from webhooks.receiver import create_receiver

from .conftest import FakeClock


def wire(fail_first=0, secret_override=None):
    clock = FakeClock()
    app = create_app(clock=clock, base_delay=30, jitter=0, max_attempts=4)
    api = TestClient(app)
    ep = api.post("/endpoints", json={"url": "http://recv.test/hook"}).json()
    receiver = create_receiver(secret_override or ep["secret"], clock=clock, fail_first=fail_first)
    rc = TestClient(receiver)

    def handler(req: httpx.Request) -> httpx.Response:
        r = rc.post(req.url.path, content=req.content, headers=dict(req.headers))
        return httpx.Response(r.status_code, content=r.content)

    app.state.dispatcher.transport = HttpxTransport(httpx.Client(transport=httpx.MockTransport(handler)))
    return api, receiver, clock


def test_real_signature_accepted_after_flaky_receiver():
    api, receiver, clock = wire(fail_first=1)
    api.post("/events", json={"type": "a.b", "data": {"x": 1}})
    api.post("/dispatch")
    clock.t += 31
    api.post("/dispatch")
    assert api.get("/deliveries").json()[0]["status"] == "succeeded"
    assert len(receiver.state.processed) == 1


def test_wrong_secret_is_rejected_as_permanent_401():
    api, receiver, _ = wire(secret_override="whsec_other")
    api.post("/events", json={"type": "a.b", "data": {}})
    api.post("/dispatch")
    d = api.get("/deliveries").json()[0]
    assert (d["status"], d["last_status_code"]) == ("dead", 401)
    assert receiver.state.processed == {}


def test_transport_error_from_httpx_is_wrapped():
    clock = FakeClock()
    app = create_app(clock=clock, jitter=0)

    def boom(req):
        raise httpx.ConnectError("refused")

    app.state.dispatcher.transport = HttpxTransport(httpx.Client(transport=httpx.MockTransport(boom)))
    api = TestClient(app)
    api.post("/endpoints", json={"url": "http://down.test/hook"})
    api.post("/events", json={"type": "a", "data": {}})
    api.post("/dispatch")
    d = api.get("/deliveries").json()[0]
    assert d["status"] == "pending" and "ConnectError" in d["last_error"]


def test_retry_after_header_parsed():
    clock = FakeClock()
    app = create_app(clock=clock, jitter=0)
    app.state.dispatcher.transport = HttpxTransport(
        httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "120"})))
    )
    api = TestClient(app)
    api.post("/endpoints", json={"url": "http://x.test/h"})
    api.post("/events", json={"type": "a", "data": {}})
    api.post("/dispatch")
    assert api.get("/deliveries").json()[0]["next_attempt_at"] == clock.t + 120
