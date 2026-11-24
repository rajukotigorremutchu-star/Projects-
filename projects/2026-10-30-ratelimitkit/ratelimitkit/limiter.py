from __future__ import annotations

import time
from typing import Callable

from .algorithms import Algorithm, Decision
from .stores import MemoryStore, Store


class Limiter:
    """Binds an algorithm to a store and a clock."""

    def __init__(self, algorithm: Algorithm, store: Store | None = None,
                 clock: Callable[[], float] = time.time, name: str = ""):
        self.algorithm = algorithm
        self.store = store if store is not None else MemoryStore()
        self.clock = clock
        self.name = name or algorithm.name

    def hit(self, key: str, cost: int = 1) -> Decision:
        if cost < 1:
            raise ValueError("cost must be >= 1")
        now = self.clock()

        def fn(state):
            out = self.algorithm.check(state, now, cost)
            return out.decision, out.state, out.ttl

        return self.store.update(f"{self.name}:{key}", fn, now)
