"""End-to-end demo with no network: a flaky signed receiver, simulated clock.

Run from the project root:  python scripts/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from webhooks.app import create_app  # noqa: E402
from webhooks.dispatcher import HttpxTransport  # noqa: E402
from webhooks.receiver import create_receiver  # noqa: E402


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def main() -> None:
    clock = Clock(1_700_000_000.0)
    app = create_app(transport=None, clock=clock, base_delay=30, jitter=0, max_attempts=4)
    api = TestClient(app)

    ep = api.post("/endpoints", json={"url": "http://receiver.local/hook", "event_types": ["order.*", "order.paid"]}).json()
    receiver = create_receiver(ep["secret"], clock=clock, fail_first=2)
    # Route outbound webhook POSTs straight into the in-process receiver.
    rc = TestClient(receiver)
    app.state.dispatcher.transport = HttpxTransport(
        httpx.Client(transport=httpx.MockTransport(lambda req: _forward(rc, req)))
    )

    ev = api.post("/events", json={"type": "order.paid", "data": {"order": 42}}, headers={"Idempotency-Key": "k1"}).json()
    again = api.post("/events", json={"type": "order.paid", "data": {"order": 42}}, headers={"Idempotency-Key": "k1"}).json()
    print("published:", ev["id"], "| duplicate publish detected:", again["duplicate"])

    for _ in range(5):
        stats = api.post("/dispatch").json()
        d = api.get("/deliveries").json()[0]
        print(f"t+{int(clock.t - 1_700_000_000):>3}s dispatch={stats} -> status={d['status']} attempts={d['attempts']}")
        if d["status"] in ("succeeded", "dead"):
            break
        clock.t += 31 * 2 ** (d["attempts"] - 1)  # jump past the backoff
    print("receiver processed:", len(receiver.state.processed), "unique event(s)")


def _forward(rc, req: httpx.Request) -> httpx.Response:
    r = rc.post(req.url.path, content=req.content, headers=dict(req.headers))
    return httpx.Response(r.status_code, content=r.content)


if __name__ == "__main__":
    main()
