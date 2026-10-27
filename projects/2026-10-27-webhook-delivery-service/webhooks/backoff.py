"""Retry schedule: capped exponential backoff with proportional jitter."""
import random
from typing import Callable


def compute_delay(
    attempt: int,
    base: float = 30.0,
    cap: float = 3600.0,
    jitter: float = 0.2,
    rng: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait after failed attempt number `attempt` (1-based).

    delay = min(cap, base * 2**(attempt-1)), then reduced by up to `jitter` (fraction) so
    that many deliveries failing together do not retry in lockstep.
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    raw = min(cap, base * (2 ** (attempt - 1)))
    return raw * (1 - jitter * rng())
