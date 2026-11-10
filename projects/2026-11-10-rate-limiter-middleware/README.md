# Rate Limiter Middleware (FastAPI / ASGI)

A small, dependency-free rate limiting library with four algorithms and a pure-ASGI middleware
that works with FastAPI/Starlette.

## Problem
APIs need per-client throttling that is correct, observable (standard headers), and testable.
Most tutorial limiters use a fixed window, which allows up to 2x the limit across a window boundary.
This project implements several algorithms behind one interface and demonstrates their trade-offs
with a deterministic simulation.

## Architecture
```
ratelimit/
  clock.py        SystemClock (monotonic) / ManualClock (deterministic tests)
  algorithms.py   Limiter base (lock, key eviction) + TokenBucket, FixedWindow,
                  SlidingWindowLog (exact), SlidingWindowCounter (O(1) approximation)
  middleware.py   ASGI middleware: rule matching by path prefix, per-rule cost,
                  RateLimit-* headers, 429 + Retry-After
app.py            demo FastAPI app          scripts/simulate.py   boundary-burst comparison
```
Every limiter returns a `Decision(allowed, limit, remaining, retry_after, reset_after)`.

## Setup
```
pip install -r requirements-dev.txt
python -m pytest
```
Python 3.11 tested.

## Usage
```python
app.add_middleware(RateLimitMiddleware, rules=[
    Rule("/search", make_limiter("sliding_log", 5, 10)),          # 5 per 10s
    Rule("/export", make_limiter("token_bucket", 10, 60), cost=5), # expensive endpoint
], key_func=lambda scope: ...)   # default: client IP
```
Run the demo: `uvicorn app:app`, then `bash samples/requests.sh` (expected: `samples/expected_responses.txt`).

Simulation: `python scripts/simulate.py` (expected output: `samples/expected_simulation.json`).
Workload: limit 10/10s; 10 requests at t=9.9s, 10 at t=10.1s, then 1 req/s.

| Algorithm | Accepted | Max in any 10s span |
|---|---|---|
| fixed_window | 30 | 20 (violates limit) |
| sliding_log | 20 | 10 |
| sliding_counter | 20 | 10 |
| token_bucket | 20 | 10 |

(Measured by the script on this workload only.)

## Design decisions
- Injectable clock: all time-dependent behavior is tested without sleeping.
- Denied requests never consume quota (no punishing retry loops).
- Fails open: a limiter exception lets the request through rather than taking the API down.
- Bounded memory: `max_keys` with eviction of idle keys first, then oldest-inserted.
- A cost larger than the limit can never succeed: `retry_after=inf`, no `Retry-After` header.
- Pure ASGI (not `BaseHTTPMiddleware`) to avoid its streaming/background-task quirks.

## Limitations
- In-memory and per-process: with multiple workers/hosts each has its own counters. A Redis
  backend (atomic Lua script) would be the next step; the `Limiter` interface is small enough to port.
- Default key is the socket peer address; behind a proxy supply a `key_func` that safely reads
  `X-Forwarded-For` (trusting it blindly is spoofable).
- `SlidingWindowCounter` is an approximation and its `retry_after` is a hint assuming no further traffic.
- `retry_after` can be 0.0 on a denial due to float rounding at an exact expiry boundary; the middleware rounds up to >=1s.
- Oldest-inserted eviction under key pressure can reset an active client's quota.
