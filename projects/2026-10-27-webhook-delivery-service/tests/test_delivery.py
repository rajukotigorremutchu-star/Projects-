import json

from webhooks import signing
from webhooks.dispatcher import SendResult, TransportError


def publish(client, type_="order.paid", key=None, data=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/events", json={"type": type_, "data": data or {"n": 1}}, headers=headers)


def only_delivery(client):
    rows = client.get("/deliveries").json()
    assert len(rows) == 1
    return rows[0]


def test_happy_path_signs_request_verifiably(client, endpoint, transport, clock):
    publish(client)
    stats = client.post("/dispatch").json()
    assert stats["succeeded"] == 1
    call = transport.calls[0]
    h = call["headers"]
    assert signing.verify(
        endpoint["secret"], h["Webhook-Id"], int(h["Webhook-Timestamp"]), call["body"],
        h["Webhook-Signature"], now=clock.t,
    )
    payload = json.loads(call["body"])
    assert payload["type"] == "order.paid" and payload["id"] == h["Webhook-Id"]
    assert only_delivery(client)["status"] == "succeeded"


def test_retry_then_success_keeps_same_id_and_body(client, endpoint, transport, clock):
    transport.script = [500, 503]
    publish(client)
    client.post("/dispatch")
    d = only_delivery(client)
    assert (d["status"], d["attempts"], d["next_attempt_at"]) == ("pending", 1, clock.t + 30)

    # Not due yet: nothing is attempted.
    assert client.post("/dispatch").json()["attempted"] == 0

    clock.t += 30
    client.post("/dispatch")
    assert only_delivery(client)["next_attempt_at"] == clock.t + 60  # exponential

    clock.t += 60
    client.post("/dispatch")
    d = client.get(f"/deliveries/{only_delivery(client)['id']}").json()
    assert d["status"] == "succeeded" and d["attempts"] == 3
    assert [a["status_code"] for a in d["attempt_log"]] == [500, 503, 200]
    bodies = {c["body"] for c in transport.calls}
    ids = {c["headers"]["Webhook-Id"] for c in transport.calls}
    assert len(bodies) == 1 and len(ids) == 1  # idempotent for the receiver
    # ...but the timestamp/signature is fresh each attempt so it stays within the replay window
    assert len({c["headers"]["Webhook-Timestamp"] for c in transport.calls}) == 3


def test_exhausting_attempts_dead_letters(client, endpoint, transport, clock):
    transport.default = 500
    publish(client)
    for _ in range(3):
        client.post("/dispatch")
        clock.t += 3600
    d = only_delivery(client)
    assert d["status"] == "dead" and d["attempts"] == 3
    assert len(transport.calls) == 3
    client.post("/dispatch")
    assert len(transport.calls) == 3  # dead means no more calls


def test_permanent_4xx_dead_immediately(client, endpoint, transport):
    transport.script = [404]
    publish(client)
    stats = client.post("/dispatch").json()
    assert stats["dead"] == 1
    d = only_delivery(client)
    assert d["status"] == "dead" and d["attempts"] == 1 and d["last_error"] == "HTTP 404"


def test_429_is_retried_and_retry_after_is_honoured(client, endpoint, transport, clock):
    transport.script = [SendResult(429, retry_after=600)]
    publish(client)
    client.post("/dispatch")
    d = only_delivery(client)
    assert d["status"] == "pending" and d["next_attempt_at"] == clock.t + 600


def test_retry_after_is_capped(app, client, endpoint, transport, clock):
    app.state.dispatcher.max_delay = 120
    transport.script = [SendResult(503, retry_after=99999)]
    publish(client)
    client.post("/dispatch")
    assert only_delivery(client)["next_attempt_at"] == clock.t + 120


def test_network_error_is_retryable(client, endpoint, transport):
    transport.script = [TransportError("ConnectTimeout: boom")]
    publish(client)
    client.post("/dispatch")
    d = only_delivery(client)
    assert d["status"] == "pending" and "ConnectTimeout" in d["last_error"]
    assert d["last_status_code"] is None


def test_redirect_is_not_success(client, endpoint, transport):
    transport.script = [302]
    publish(client)
    client.post("/dispatch")
    assert only_delivery(client)["status"] == "dead"  # 3xx: not 2xx and not retryable


def test_idempotency_key_dedupes_fanout(client, endpoint):
    first = publish(client, key="abc")
    second = publish(client, key="abc")
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["id"] == second.json()["id"] and second.json()["duplicate"] is True
    assert len(client.get("/deliveries").json()) == 1
    assert publish(client, key="other").status_code == 201


def test_fanout_respects_event_type_patterns_and_disabled(client, transport):
    all_ep = client.post("/endpoints", json={"url": "https://a.test"}).json()
    orders = client.post("/endpoints", json={"url": "https://b.test", "event_types": ["order.*"]}).json()
    exact = client.post("/endpoints", json={"url": "https://c.test", "event_types": ["user.created"]}).json()
    client.post(f"/endpoints/{all_ep['id']}/disable")
    publish(client, "order.paid")
    rows = client.get("/deliveries").json()
    assert {r["endpoint_id"] for r in rows} == {orders["id"]}
    publish(client, "user.created")
    assert {r["endpoint_id"] for r in client.get("/deliveries").json()} == {orders["id"], exact["id"]}


def test_endpoint_disabled_after_fanout_parks_delivery(client, endpoint, transport):
    publish(client)
    client.post(f"/endpoints/{endpoint['id']}/disable")
    client.post("/dispatch")
    assert transport.calls == []
    assert only_delivery(client)["status"] == "dead"


def test_replay_dead_delivery(client, endpoint, transport, clock):
    transport.script = [400]
    publish(client)
    client.post("/dispatch")
    did = only_delivery(client)["id"]
    assert client.post(f"/deliveries/{did}/replay").status_code == 200
    d = client.get(f"/deliveries/{did}").json()
    assert d["status"] == "pending" and d["attempts"] == 0
    client.post("/dispatch")
    d = client.get(f"/deliveries/{did}").json()
    assert d["status"] == "succeeded"
    assert [a["status_code"] for a in d["attempt_log"]] == [400, 200]  # history preserved


def test_replay_only_for_dead(client, endpoint):
    publish(client)
    did = only_delivery(client)["id"]
    assert client.post(f"/deliveries/{did}/replay").status_code == 409
    assert client.post("/deliveries/nope/replay").status_code == 409


def test_in_flight_rows_recovered_on_restart(tmp_path, transport, clock):
    from webhooks.app import create_app
    from webhooks.store import Store
    from fastapi.testclient import TestClient

    path = str(tmp_path / "w.db")
    c = TestClient(create_app(store=Store(path), transport=transport, clock=clock))
    c.post("/endpoints", json={"url": "https://x.test"})
    publish(c)
    store = c.app.state.store
    assert len(store.claim_due(clock.t, 10)) == 1  # simulate crash after claim, before result
    assert c.get("/deliveries?status=in_flight").json()[0]["status"] == "in_flight"

    c2 = TestClient(create_app(store=Store(path), transport=transport, clock=clock))
    assert c2.post("/dispatch").json()["succeeded"] == 1


def test_validation_and_secret_exposure(client):
    assert client.post("/endpoints", json={"url": "ftp://x"}).status_code == 422
    assert client.post("/endpoints", json={"url": "not a url"}).status_code == 422
    assert client.post("/endpoints", json={"url": "https://x.test", "event_types": []}).status_code == 422
    created = client.post("/endpoints", json={"url": "https://x.test"}).json()
    assert created["secret"].startswith("whsec_")
    assert all("secret" not in e for e in client.get("/endpoints").json())
    assert client.post("/events", json={"type": "", "data": {}}).status_code == 422
    assert client.get("/deliveries?status=bogus").status_code == 422
    assert client.get("/deliveries/missing").status_code == 404
    assert client.post("/endpoints/missing/disable").status_code == 404
