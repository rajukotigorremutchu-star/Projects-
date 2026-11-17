"""Rate limiting algorithms. All are per-key, thread-safe, and clock-injectable.

Each limiter allows `limit` units per `window` seconds (token bucket: refill
rate limit/window with burst capacity `limit`).
"""
import math
import threading
from collections import deque
from dataclasses import dataclass

from .clock import SystemClock


@dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: float  # seconds until a request of the same cost could succeed (0 if allowed)
    reset_after: float  # seconds until the key is back to full quota


class Limiter:
    def __init__(self, limit: int, window: float, clock=None, max_keys: int = 100_000):
        if limit <= 0 or window <= 0:
            raise ValueError("limit and window must be positive")
        self.limit, self.window = limit, float(window)
        self.clock = clock or SystemClock()
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._state: dict = {}

    def check(self, key: str, cost: int = 1) -> Decision:
        if cost <= 0:
            raise ValueError("cost must be positive")
        with self._lock:
            now = self.clock.now()
            if key not in self._state and len(self._state) >= self.max_keys:
                self._evict(now)
            return self._check(key, cost, now)

    def _check(self, key, cost, now) -> Decision:
        raise NotImplementedError

    def _idle(self, st, now) -> bool:
        raise NotImplementedError

    def _evict(self, now):
        """Drop keys whose state is equivalent to a fresh key; if none, drop oldest."""
        for k in [k for k, s in self._state.items() if self._idle(s, now)]:
            del self._state[k]
        if len(self._state) >= self.max_keys:  # still full: drop oldest inserted
            del self._state[next(iter(self._state))]


class TokenBucket(Limiter):
    def _check(self, key, cost, now):
        rate = self.limit / self.window
        tokens, last = self._state.get(key, (float(self.limit), now))
        tokens = min(self.limit, tokens + (now - last) * rate)
        if cost > self.limit:
            return Decision(False, self.limit, int(tokens), math.inf, (self.limit - tokens) / rate)
        if tokens >= cost:
            tokens -= cost
            self._state[key] = (tokens, now)
            return Decision(True, self.limit, int(tokens), 0.0, (self.limit - tokens) / rate)
        self._state[key] = (tokens, now)
        return Decision(False, self.limit, int(tokens), (cost - tokens) / rate,
                        (self.limit - tokens) / rate)

    def _idle(self, st, now):
        return st[0] + (now - st[1]) * self.limit / self.window >= self.limit


class FixedWindow(Limiter):
    def _check(self, key, cost, now):
        idx = math.floor(now / self.window)
        win, used = self._state.get(key, (idx, 0))
        if win != idx:
            win, used = idx, 0
        end = (idx + 1) * self.window - now
        if used + cost <= self.limit:
            used += cost
            self._state[key] = (win, used)
            return Decision(True, self.limit, self.limit - used, 0.0, end)
        self._state[key] = (win, used)
        return Decision(False, self.limit, max(0, self.limit - used), end, end)

    def _idle(self, st, now):
        return st[0] != math.floor(now / self.window)


class SlidingWindowLog(Limiter):
    """Exact: stores a timestamp per accepted unit. O(limit) memory per key."""

    def _check(self, key, cost, now):
        log = self._state.setdefault(key, deque())
        while log and log[0] <= now - self.window:
            log.popleft()
        reset = (log[-1] + self.window - now) if log else 0.0
        if len(log) + cost <= self.limit:
            log.extend([now] * cost)
            return Decision(True, self.limit, self.limit - len(log), 0.0,
                            log[-1] + self.window - now)
        # need (len+cost-limit) oldest entries to expire
        need = len(log) + cost - self.limit
        retry = log[need - 1] + self.window - now if need <= len(log) else math.inf
        return Decision(False, self.limit, self.limit - len(log), retry, reset)

    def _idle(self, st, now):
        return not st or st[-1] <= now - self.window


class SlidingWindowCounter(Limiter):
    """Approximation: weights previous fixed window by its remaining overlap. O(1) memory."""

    def _check(self, key, cost, now):
        idx = math.floor(now / self.window)
        cur_idx, cur, prev = self._state.get(key, (idx, 0, 0))
        if idx == cur_idx + 1:
            cur_idx, cur, prev = idx, 0, cur
        elif idx > cur_idx + 1:
            cur_idx, cur, prev = idx, 0, 0
        frac = (now - idx * self.window) / self.window
        est = prev * (1 - frac) + cur
        end = (idx + 1) * self.window - now
        if est + cost <= self.limit:
            cur += cost
            self._state[key] = (cur_idx, cur, prev)
            return Decision(True, self.limit, max(0, int(self.limit - est - cost)), 0.0, end)
        self._state[key] = (cur_idx, cur, prev)
        # Earliest moment the weighted estimate fits (a hint: assumes no further traffic).
        retry = end
        if prev > 0 and cur + cost <= self.limit:
            f_needed = 1 - (self.limit - cur - cost) / prev
            retry = min(end, max(0.0, (f_needed - frac) * self.window))
        return Decision(False, self.limit, max(0, int(self.limit - est)), retry, end)

    def _idle(self, st, now):
        return math.floor(now / self.window) > st[0] + 1


ALGORITHMS = {"token_bucket": TokenBucket, "fixed_window": FixedWindow,
              "sliding_log": SlidingWindowLog, "sliding_counter": SlidingWindowCounter}


def make_limiter(name: str, limit: int, window: float, clock=None, **kw) -> Limiter:
    try:
        return ALGORITHMS[name](limit, window, clock, **kw)
    except KeyError:
        raise ValueError(f"unknown algorithm {name!r}; choose from {sorted(ALGORITHMS)}")
