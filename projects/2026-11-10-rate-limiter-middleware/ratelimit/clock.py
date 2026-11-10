import time


class SystemClock:
    def now(self) -> float:
        return time.monotonic()


class ManualClock:
    """Deterministic clock for tests and simulations."""

    def __init__(self, start: float = 0.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
