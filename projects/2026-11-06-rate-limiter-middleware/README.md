# Rate Limiter Middleware (FastAPI / ASGI)

A small, dependency-light rate limiting library with four interchangeable algorithms and a pure-ASGI middleware, plus a trace simulator that makes the algorithms' behavioural differences measurable.

## Problem
APIs need per-client request quotas, but the algorithm choice matters: fixed windows are cheap yet allow 2x bursts at window boundaries; exact sliding logs are accurate but memory-hungry; token buckets allow controlled bursts. This project implements all four behind one interface and lets you compare them on the same traffic.

## Architecture
```
ratelimiter/algorithms.py  FixedWindow, SlidingWindowLog, SlidingWindowCounter, TokenBucket
                           (common Limiter.check(key, cost) -> Decision; injectable clock; thread-safe)
ratelimiter/middleware.py  Pure ASGI middleware: per-rule limiters (longest path prefix wins),
                           key = X-API-Key header else client IP, exempt paths,
                           X-RateLimit-* headers, 429 + Retry-After
app.py                     Demo FastAPI app with three rules
simulate.py                Replays a CSV trace (timestamp,key) through each algorithm
samples/                   trace.csv + expected_output.json
tests/                     algorithms, middleware (FastAPI TestClient), simulator regression
```

## Setup
```
python -m pip install -r requirements.txt
```

## Usage
```
python -m pytest -q
python simulate.py samples/trace.csv --limit 5 --window 10
uvicorn app:app --port 8000     # /login: 3 per 60s (exact), /search: token bucket, / : 100/min
```
Library use:
```python
from ratelimiter import make_limiter
lim = make_limiter("token_bucket", limit=10, window=1)
d = lim.check("user-1"); d.allowed, d.remaining, d.retry_after
```

### Sample simulation (measured, `samples/expected_output.json`)
Trace: 5 requests at t=9.0–9.4, 5 at t=10.0–10.4 (window boundary), then 1 req/s for 10s. Limit 5 per 10s.

| algorithm | allowed | rejected | max accepted in any 10s window |
|---|---|---|---|
| fixed_window | 15 | 5 | **10** (boundary burst, 2x the limit) |
| sliding_log | 10 | 10 | 5 |
| sliding_counter | 10 | 10 | 5 |
| token_bucket | 14 | 6 | 9 (burst of 5 + refill) |

## Design decisions
- Injectable clock instead of sleeping in tests: all timing tests are deterministic.
- Limiters are keyed `"<rule prefix>|<client>"`, so each rule has an independent quota per client.
- The sliding-window counter is an approximation (weights the previous window); tests document that it can need up to two windows to fully recover.
- Pure ASGI rather than `BaseHTTPMiddleware` to avoid its body/streaming overhead and quirks.

## Limitations
- State is in-process memory: not shared across workers/hosts and lost on restart (a Redis/Lua store would be the next step). Idle keys are never evicted.
- Client IP is taken from the socket peer; behind a proxy you must supply a `key_func` reading a trusted forwarded header.
- A single global lock per limiter; fine for demos, not tuned for very high throughput.
- Only HTTP scopes are limited (not WebSockets).

## Verified
`python -m pytest -q`: 28 passed. Live uvicorn smoke test: POST /login returned 200,200,200,429 with `Retry-After` and `X-RateLimit-*` headers.
