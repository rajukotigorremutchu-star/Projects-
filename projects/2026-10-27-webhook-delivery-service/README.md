# Webhook Delivery Service

A small, self-hosted service that delivers events to customer webhook URLs **reliably and verifiably**: HMAC-signed requests, retries with capped exponential backoff and jitter, `Retry-After` support, a dead-letter state with manual replay, and idempotent publishing. FastAPI + SQLite; no external services.

## Problem

Calling a customer's URL once and hoping is how events get lost. Receivers go down, rate-limit, or deploy bad code; senders crash mid-request; publishers retry and double-send. This project implements the delivery side of that contract, plus a reference receiver showing how to consume it safely.

## Architecture

```
POST /events ──► events + deliveries (fan-out by endpoint patterns)   [SQLite]
                         │
   POST /dispatch  or  python -m webhooks.worker
                         ▼
   Dispatcher.run_once: claim due rows (pending → in_flight)
        → sign → Transport.send → classify outcome
             2xx                       → succeeded
             network err / 5xx / 408,409,425,429 → pending again at now + backoff (or dead at max_attempts)
             other 3xx / 4xx           → dead immediately
```

| File | Role |
|------|------|
| `webhooks/signing.py` | `v1=<hmac-sha256>` over `"{id}.{timestamp}.{body}"`; constant-time verify with replay window (300 s) |
| `webhooks/backoff.py` | `min(cap, base·2^(n-1))` reduced by up to `jitter` |
| `webhooks/store.py` | SQLite schema, fan-out, atomic claim, attempt log, replay |
| `webhooks/dispatcher.py` | Delivery engine; `Transport` protocol with an httpx implementation |
| `webhooks/app.py` | FastAPI API |
| `webhooks/worker.py` | Polling loop for real deployments |
| `webhooks/receiver.py` | Reference consumer: verifies signature, de-duplicates by `Webhook-Id` |

Request headers sent: `Webhook-Id` (the event id, **stable across retries** so receivers can de-duplicate), `Webhook-Timestamp` and `Webhook-Signature` (**fresh each attempt** so retries hours later still pass the replay window). The JSON body is rendered once and re-sent byte-for-byte.

## Setup

```bash
cd projects/2026-10-27-webhook-delivery-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Usage

```bash
# API (in-memory DB by default; set WEBHOOKS_DB=webhooks.db to persist)
uvicorn webhooks.app:create_app --factory --port 8000
# worker loop (shares the same WEBHOOKS_DB file)
WEBHOOKS_DB=webhooks.db python -m webhooks.worker
```

Requests are in `samples/requests.http`. Endpoints:

| Method & path | Purpose |
|---|---|
| `POST /endpoints` | Register `{url, event_types}` (fnmatch patterns, default `["*"]`); returns the signing secret **once** |
| `GET /endpoints`, `POST /endpoints/{id}/disable` | List (no secrets) / disable |
| `POST /events` | Publish `{type, data}`; optional `Idempotency-Key` header (201 new, 200 duplicate) |
| `GET /deliveries?status=`, `GET /deliveries/{id}` | Inspect state and per-attempt log |
| `POST /deliveries/{id}/replay` | Requeue a dead delivery |
| `POST /dispatch` | Run one delivery cycle now |

Offline demo (flaky receiver that fails twice, simulated clock): `python scripts/demo.py` — output in `samples/demo_output.txt`:

```
published: evt_<id> | duplicate publish detected: True
t+  0s dispatch={'attempted': 1, 'succeeded': 0, 'retried': 1, 'dead': 0} -> status=pending attempts=1
t+ 31s dispatch={'attempted': 1, 'succeeded': 0, 'retried': 1, 'dead': 0} -> status=pending attempts=2
t+ 93s dispatch={'attempted': 1, 'succeeded': 1, 'retried': 0, 'dead': 0} -> status=succeeded attempts=3
receiver processed: 1 unique event(s)
```

### Verifying on the receiving side

```python
from webhooks.signing import verify
ok = verify(secret, headers["Webhook-Id"], int(headers["Webhook-Timestamp"]), raw_body,
            headers["Webhook-Signature"], now=time.time())
```
Verify against the **raw** body bytes, then de-duplicate on `Webhook-Id`.

## Tests

```bash
pytest -q
```
29 tests cover: signature known-vector, tamper/replay rejection; backoff growth, cap and jitter; happy path with a verifiable signature; retry schedule; dead-lettering at max attempts; permanent vs retryable status classification; `Retry-After` (honoured and capped); network errors; idempotent publish; fan-out patterns and disabled endpoints; replay with preserved history; crash recovery of `in_flight` rows across a restart (on-disk DB); input validation and secret non-disclosure; and end-to-end runs through the real httpx transport into the reference receiver. The API was also smoke-tested manually with uvicorn + curl (delivery to a closed port was recorded as a retryable `ConnectError`).

## Design decisions

- **At-least-once delivery.** Exactly-once is not achievable over HTTP; stable `Webhook-Id` makes receivers' de-duplication easy.
- **Fresh timestamp per attempt, stable id/body.** Keeps signatures valid inside the replay window without changing what is being delivered.
- **Time and transport are injected** (`clock`, `Transport`), so retry behaviour is tested deterministically with no sleeping or sockets.
- **3xx is permanent**, since redirects are not followed (a redirect could move signed payloads to another host).
- **`in_flight` state + restart recovery** instead of holding DB transactions open during HTTP calls.
- **Dispatch is pull-based** (`/dispatch` or the worker) rather than an in-process background thread, which keeps the API testable and the failure modes explicit.

## Limitations

- Single process / single SQLite connection. Running several workers against one DB file would need row-level claim leasing (a crash-recovery sweep only runs at startup).
- No authentication on the management API and no SSRF protection (only `http(s)` is enforced); do not expose it or let untrusted users choose URLs without adding both, plus a private-IP block-list.
- Secrets are stored in plaintext in SQLite; no secret rotation.
- `Retry-After` HTTP-date form is ignored (seconds only). Deliveries are sent sequentially, with no per-endpoint concurrency limits or circuit breaker.
- No retention/cleanup of old events and attempts.
