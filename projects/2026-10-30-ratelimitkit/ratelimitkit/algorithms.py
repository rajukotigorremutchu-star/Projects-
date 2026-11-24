"""Pure rate-limiting algorithms.

Each algorithm is a pure function of (state, now, cost): it returns a
``Decision`` plus the next state. State is always JSON-serialisable so any
store can persist it. Keeping algorithms free of I/O and clocks makes them
deterministic to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    # Seconds until a request of the same cost would be allowed (0 if allowed,
    # None if it can never be allowed, e.g. cost > limit).
    retry_after: float | None
    # Seconds until the limiter is back to full capacity.
    reset_after: float


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    state: Any
    ttl: float  # how long the state is worth keeping


class Algorithm(Protocol):
    name: str

    def check(self, state: Any, now: float, cost: int) -> Outcome: ...


def _never(limit: int, state: Any, ttl: float) -> Outcome:
    return Outcome(Decision(False, limit, 0, None, ttl), state, ttl)


class TokenBucket:
    """Bucket of ``capacity`` tokens refilled at ``refill_rate`` tokens/second."""

    name = "token_bucket"

    def __init__(self, capacity: int, refill_rate: float):
        if capacity < 1 or refill_rate <= 0:
            raise ValueError("capacity must be >= 1 and refill_rate > 0")
        self.capacity = capacity
        self.refill_rate = refill_rate

    def check(self, state, now, cost):
        tokens, last = (float(self.capacity), now) if state is None else state
        tokens = min(self.capacity, tokens + max(0.0, now - last) * self.refill_rate)
        ttl = self.capacity / self.refill_rate
        if cost > self.capacity:
            return _never(self.capacity, [tokens, now], ttl)
        if tokens >= cost:
            tokens -= cost
            dec = Decision(True, self.capacity, int(tokens), 0.0,
                           (self.capacity - tokens) / self.refill_rate)
        else:
            dec = Decision(False, self.capacity, int(tokens),
                           (cost - tokens) / self.refill_rate,
                           (self.capacity - tokens) / self.refill_rate)
        return Outcome(dec, [tokens, now], ttl)


class FixedWindow:
    """Counter that resets at fixed window boundaries (cheap, but bursty at edges)."""

    name = "fixed_window"

    def __init__(self, limit: int, window: float):
        if limit < 1 or window <= 0:
            raise ValueError("limit must be >= 1 and window > 0")
        self.limit = limit
        self.window = window

    def check(self, state, now, cost):
        idx = math.floor(now / self.window)
        count = state[1] if state is not None and state[0] == idx else 0
        window_end = (idx + 1) * self.window
        if cost > self.limit:
            return _never(self.limit, [idx, count], self.window)
        if count + cost <= self.limit:
            count += cost
            dec = Decision(True, self.limit, self.limit - count, 0.0, window_end - now)
        else:
            dec = Decision(False, self.limit, self.limit - count,
                           window_end - now, window_end - now)
        return Outcome(dec, [idx, count], self.window)


class SlidingWindowLog:
    """Exact sliding window: remembers every accepted request timestamp."""

    name = "sliding_log"

    def __init__(self, limit: int, window: float):
        if limit < 1 or window <= 0:
            raise ValueError("limit must be >= 1 and window > 0")
        self.limit = limit
        self.window = window

    def check(self, state, now, cost):
        log = [t for t in (state or []) if t > now - self.window]
        if cost > self.limit:
            return _never(self.limit, log, self.window)
        used = len(log)
        if used + cost <= self.limit:
            log.extend([now] * cost)
            used += cost
            dec = Decision(True, self.limit, self.limit - used, 0.0,
                           log[0] + self.window - now)
        else:
            # Allowed once enough of the oldest entries have expired.
            expire = log[used + cost - self.limit - 1] + self.window
            dec = Decision(False, self.limit, self.limit - used,
                           expire - now, log[0] + self.window - now)
        return Outcome(dec, log, self.window)


class SlidingWindowCounter:
    """Approximate sliding window using two fixed-window counters.

    Estimate = previous_count * (1 - elapsed_fraction) + current_count.
    O(1) memory per key; assumes requests in the previous window were evenly
    spread, so it can be slightly off for bursty traffic.
    """

    name = "sliding_counter"

    def __init__(self, limit: int, window: float):
        if limit < 1 or window <= 0:
            raise ValueError("limit must be >= 1 and window > 0")
        self.limit = limit
        self.window = window

    def _roll(self, state, idx):
        if state is None:
            return 0, 0
        s_idx, cur, prev = state
        if s_idx == idx:
            return cur, prev
        if s_idx == idx - 1:
            return 0, cur
        return 0, 0

    def check(self, state, now, cost):
        idx = math.floor(now / self.window)
        cur, prev = self._roll(state, idx)
        frac = (now - idx * self.window) / self.window
        estimate = prev * (1 - frac) + cur
        ttl = 2 * self.window
        if cost > self.limit:
            return _never(self.limit, [idx, cur, prev], ttl)
        reset_after = (idx + 1) * self.window - now + (self.window if cur else 0)
        if estimate + cost <= self.limit + 1e-9:
            cur += cost
            used = math.ceil(prev * (1 - frac) + cur - 1e-9)
            dec = Decision(True, self.limit, max(0, self.limit - used), 0.0, reset_after)
        else:
            dec = Decision(False, self.limit,
                           max(0, int(self.limit - math.ceil(estimate - 1e-9))),
                           self._retry_after(idx, now, cur, prev, cost), reset_after)
        return Outcome(dec, [idx, cur, prev], ttl)

    def _retry_after(self, idx, now, cur, prev, cost):
        w = self.window
        room = self.limit - cost - cur  # max previous-window weight that still fits
        if room >= 0 and prev > 0:
            when = idx * w + (1 - room / prev) * w
        else:
            # Current window is already too full: wait for the next one, where
            # today's count becomes the (decaying) previous-window count.
            room = self.limit - cost
            frac = max(0.0, 1 - room / cur) if cur > 0 else 0.0
            when = (idx + 1) * w + frac * w
        return max(0.0, when - now)


_REGISTRY = {
    "token_bucket": TokenBucket,
    "fixed_window": FixedWindow,
    "sliding_log": SlidingWindowLog,
    "sliding_counter": SlidingWindowCounter,
}


def build_algorithm(spec: dict) -> Algorithm:
    """Build an algorithm from ``{"type": "token_bucket", ...kwargs}``."""
    spec = dict(spec)
    try:
        cls = _REGISTRY[spec.pop("type")]
    except KeyError as exc:
        raise ValueError(f"unknown or missing algorithm type; choose from {sorted(_REGISTRY)}") from exc
    return cls(**spec)
