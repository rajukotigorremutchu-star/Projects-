import pytest
from fastapi.testclient import TestClient

from webhooks.app import create_app
from webhooks.dispatcher import SendResult, TransportError


class FakeClock:
    def __init__(self, t=1_700_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeTransport:
    """Replays scripted outcomes: an int status, a SendResult, or an Exception to raise."""

    def __init__(self, script=None, default=200):
        self.script = list(script or [])
        self.default = default
        self.calls = []

    def send(self, url, headers, body, timeout):
        self.calls.append({"url": url, "headers": headers, "body": body})
        out = self.script.pop(0) if self.script else self.default
        if isinstance(out, Exception):
            raise out
        return out if isinstance(out, SendResult) else SendResult(out)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def transport():
    return FakeTransport()


@pytest.fixture
def app(clock, transport):
    return create_app(transport=transport, clock=clock, base_delay=30, jitter=0, max_attempts=3)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def endpoint(client):
    return client.post("/endpoints", json={"url": "https://example.test/hook"}).json()


__all__ = ["TransportError"]
