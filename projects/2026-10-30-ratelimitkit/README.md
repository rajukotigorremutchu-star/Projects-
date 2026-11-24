# ratelimitkit

A small, dependency-light rate-limiting library with four algorithms, two
interchangeable state stores (in-memory, SQLite) and a pure-ASGI middleware
for FastAPI/Starlette. Includes a CLI that replays a request trace through
several algorithms so their behaviour can be compared side by side.

## Problem

"Add a rate limiter" is easy to get subtly wrong: window-boundary bursts,
inaccurate `Retry-After`, races between workers, and a limiter outage taking
the whole API down. This project implements the common algorithms as pure,
clock-injected functions so those properties can be tested deterministically.

## Architecture

```
Request ─▶ RateLimitMiddleware ─▶ Rule (path/method) ─▶ Limiter.hit(key, cost)
                                                          │  clock()
                                                          ▼
                                       Store.update(key, fn)   (atomic read-modify-write)
                                                          │
                                                          ▼
                                    Algorithm.check(state, now, cost) -> Decision, new state
```

| Module | Role |
|---|---|
| `algorithms.py` | `TokenBucket`, `FixedWindow`, `SlidingWindowLog`, `SlidingWindowCounter` – pure functions of `(state, now, cost)`; state is JSON-serialisable |
| `stores.py` | `MemoryStore` (lock + TTL sweep), `SQLiteStore` (`BEGIN IMMEDIATE` transactions, shared across processes) |
| `limiter.py` | Binds algorithm + store + clock |
| `asgi.py` | Middleware: first matching `Rule` wins; adds `RateLimit-*` headers; 429 + `Retry-After`; fail-open/closed |
| `cli.py` | `simulate` – replay a JSON trace against multiple limiters |

## Setup

```bash
cd projects/2026-10-30-ratelimitkit
python -m pip install -r requirements-dev.txt   # Python 3.11
python -m pytest -q
```

## Usage

Library:

```python
from ratelimitkit import Limiter, TokenBucket
lim = Limiter(TokenBucket(capacity=5, refill_rate=1.0))   # 5 burst, 1 req/s sustained
d = lim.hit("user-42")           # Decision(allowed, limit, remaining, retry_after, reset_after)
```

FastAPI (`examples/demo_app.py`, SQLite store shared by all workers):

```bash
uvicorn examples.demo_app:app
for i in 1 2 3 4 5 6 7; do curl -s -o /dev/null -w "%{http_code} " "localhost:8000/search?q=x"; done
# 200 200 200 200 200 429 429     (observed when run locally)
```

Clients are keyed by `X-API-Key` header, else client IP (`key_func` is pluggable).

Trace replay:

```bash
python -m ratelimitkit simulate samples/scenario.json --summary
python -m ratelimitkit simulate samples/scenario.json > /tmp/out.json   # full per-request detail
```

`samples/scenario.json` is a 3-requests-per-10s policy applied to 12 requests
(key `a` sends 10, key `b` sends two cost-2 requests). `samples/expected_output.json`
is the full output; its allowed counts were checked by hand for the fixed-window
and sliding-log cases and are also asserted in tests:

| Algorithm | Allowed / 12 |
|---|---|
| fixed_window | 8 |
| sliding_log | 8 |
| sliding_counter | 6 |
| token_bucket (3 burst, 0.3/s) | 9 |

## Tests

60 tests, including:
- Per-algorithm invariants: limit enforcement, key isolation, weighted cost, cost > limit.
- **`retry_after` accuracy**: after a denial, waiting `retry_after` must allow the request, and waiting slightly less must not (all algorithms, several window offsets).
- Fixed-window 2× boundary burst vs. exact sliding log.
- Concurrency: 16 threads × 10 hits against limit 20 allow exactly 20 (both stores); two `SQLiteStore` instances on one file share state like separate workers.
- Middleware: headers, 429 body, recovery, rule ordering, method filter, API-key isolation, fail-open vs fail-closed.
- Golden-file test for the CLI.

## Design decisions

- **Pure algorithms + injected clock**: no `sleep` in tests; time is just a number.
- **Atomic `update(key, fn)` store API** rather than get/set, so correctness doesn't depend on caller locking.
- **Wall-clock `time.time` default**, because monotonic clocks aren't comparable across processes sharing a SQLite file.
- **Fail-open by default**: a broken store shouldn't take down the API; configurable.
- Denied requests don't consume budget (log / counters unchanged); the token bucket only refills its timestamp.
- The sliding counter's `retry_after` is computed analytically (current window, else next window) instead of a conservative guess.

## Limitations

- SQLite serialises writers; it is fine for a few workers on one host, not a replacement for Redis at scale. No Redis store is provided (the `Store` protocol makes one straightforward).
- `SlidingWindowLog` memory is O(limit) per key; `SlidingWindowCounter` is an approximation.
- Middleware offloads each check to a thread pool; this adds latency per request and was not benchmarked.
- Clock skew between hosts sharing one store is not handled.
- Keys behind a proxy need a custom `key_func` (no `X-Forwarded-For` parsing, deliberately, since it is spoofable).
- Expired SQLite rows are only removed by calling `purge_expired`.
