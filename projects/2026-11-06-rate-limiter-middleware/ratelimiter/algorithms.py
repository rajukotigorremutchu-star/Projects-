"""In-memory rate limiting algorithms with an injectable clock (deterministic tests)."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Tuple

Clock = Callable[[], float]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float  # seconds until a retry could succeed (0 if allowed)
    reset_after: float  # seconds until the quota is fully restored


class Limiter:
    """Allow at most `limit` units per `window` seconds per key."""

    name = "base"

    def __init__(self, limit: int, window: float, clock: Clock = time.monotonic):
        if limit <= 0 or window <= 0:
            raise ValueError("limit and window must be positive")
        self.limit, self.window, self.clock = limit, float(window), clock
        self._lock = threading.Lock()

    def check(self, key: str, cost: int = 1) -> Decision:
        if cost <= 0:
            raise ValueError("cost must be positive")
        with self._lock:
            return self._check(key, cost, self.clock())

    def _check(self, key: str, cost: int, now: float) -> Decision:  # pragma: no cover
        raise NotImplementedError


class FixedWindow(Limiter):
    """Counter reset at window boundaries. Cheap, but allows 2x bursts across a boundary."""

    name = "fixed_window"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._state: Dict[str, Tuple[int, int]] = {}  # key -> (window_index, count)

    def _check(self, key, cost, now):
        idx = math.floor(now / self.window)
        win, count = self._state.get(key, (idx, 0))
        if win != idx:
            win, count = idx, 0
        reset = (idx + 1) * self.window - now
        if count + cost > self.limit:
            self._state[key] = (win, count)
            return Decision(False, self.limit, max(self.limit - count, 0), reset, reset)
        count += cost
        self._state[key] = (win, count)
        return Decision(True, self.limit, self.limit - count, 0.0, reset)


class SlidingWindowLog(Limiter):
    """Exact: stores a timestamp per accepted unit. Accurate, O(limit) memory per key."""

    name = "sliding_log"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._log: Dict[str, Deque[float]] = {}

    def _check(self, key, cost, now):
        log = self._log.setdefault(key, deque())
        while log and log[0] <= now - self.window:
            log.popleft()
        used = len(log)
        if used + cost > self.limit:
            # need enough old entries to expire so that used - expired + cost <= limit
            need = used + cost - self.limit
            retry = log[need - 1] + self.window - now if need <= used else self.window
            reset = log[-1] + self.window - now if log else 0.0
            return Decision(False, self.limit, max(self.limit - used, 0), max(retry, 0.0), reset)
        log.extend([now] * cost)
        return Decision(True, self.limit, self.limit - len(log), 0.0, log[-1] + self.window - now)


class SlidingWindowCounter(Limiter):
    """Approximation: weights the previous fixed window by its overlap. O(1) memory."""

    name = "sliding_counter"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._state: Dict[str, Tuple[int, int, int]] = {}  # idx, current, previous

    def _check(self, key, cost, now):
        idx = math.floor(now / self.window)
        win, cur, prev = self._state.get(key, (idx, 0, 0))
        if win != idx:
            prev = cur if idx - win == 1 else 0
            win, cur = idx, 0
        elapsed = now - idx * self.window
        weight = 1.0 - elapsed / self.window
        estimate = prev * weight + cur
        if estimate + cost > self.limit:
            self._state[key] = (win, cur, prev)
            if prev and prev * weight > 0:
                # time until the weighted previous window shrinks enough
                excess = estimate + cost - self.limit
                retry = min(excess / prev * self.window, self.window - elapsed)
            else:
                retry = self.window - elapsed
            return Decision(False, self.limit, max(int(self.limit - estimate), 0),
                            max(retry, 0.0), self.window)
        cur += cost
        self._state[key] = (win, cur, prev)
        return Decision(True, self.limit, max(int(self.limit - (prev * weight + cur)), 0),
                        0.0, self.window)


class TokenBucket(Limiter):
    """Bucket of `limit` tokens refilled at limit/window per second. Allows bursts up to `limit`."""

    name = "token_bucket"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.rate = self.limit / self.window
        self._state: Dict[str, Tuple[float, float]] = {}  # tokens, last

    def _check(self, key, cost, now):
        tokens, last = self._state.get(key, (float(self.limit), now))
        tokens = min(self.limit, tokens + max(now - last, 0.0) * self.rate)
        if cost > tokens:
            self._state[key] = (tokens, now)
            retry = (cost - tokens) / self.rate if cost <= self.limit else math.inf
            return Decision(False, self.limit, int(tokens), retry, (self.limit - tokens) / self.rate)
        tokens -= cost
        self._state[key] = (tokens, now)
        return Decision(True, self.limit, int(tokens), 0.0, (self.limit - tokens) / self.rate)


ALGORITHMS = {c.name: c for c in (FixedWindow, SlidingWindowLog, SlidingWindowCounter, TokenBucket)}


def make_limiter(name: str, limit: int, window: float, clock: Clock = time.monotonic) -> Limiter:
    try:
        return ALGORITHMS[name](limit, window, clock=clock)
    except KeyError:
        raise ValueError(f"unknown algorithm {name!r}; choose from {sorted(ALGORITHMS)}") from None
