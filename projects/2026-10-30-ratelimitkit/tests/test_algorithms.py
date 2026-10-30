import pytest

from ratelimitkit import (FixedWindow, Limiter, MemoryStore, SlidingWindowCounter,
                          SlidingWindowLog, TokenBucket, build_algorithm)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


ALGOS = {
    "token_bucket": lambda: TokenBucket(5, 0.5),
    "fixed_window": lambda: FixedWindow(5, 10),
    "sliding_log": lambda: SlidingWindowLog(5, 10),
    "sliding_counter": lambda: SlidingWindowCounter(5, 10),
}


def make(name, clock):
    return Limiter(ALGOS[name](), MemoryStore(), clock)


@pytest.mark.parametrize("name", ALGOS)
def test_allows_up_to_limit_then_denies(name):
    clock = Clock(1005.0)
    lim = make(name, clock)
    decisions = [lim.hit("k") for _ in range(7)]
    assert [d.allowed for d in decisions] == [True] * 5 + [False] * 2
    assert [d.remaining for d in decisions[:5]] == [4, 3, 2, 1, 0]


@pytest.mark.parametrize("name", ALGOS)
def test_keys_are_independent(name):
    lim = make(name, Clock())
    for _ in range(5):
        lim.hit("a")
    assert not lim.hit("a").allowed
    assert lim.hit("b").allowed


@pytest.mark.parametrize("name", ALGOS)
@pytest.mark.parametrize("offset", [0.0, 1.3, 4.9, 7.77])
def test_waiting_retry_after_makes_request_allowed(name, offset):
    """Invariant: a denial's retry_after is accurate (never too short)."""
    clock = Clock(1000.0 + offset)
    lim = make(name, clock)
    for _ in range(5):
        lim.hit("k")
        clock.t += 0.1
    d = lim.hit("k")
    assert not d.allowed and d.retry_after > 0
    clock.t += d.retry_after + 1e-6
    assert lim.hit("k").allowed


@pytest.mark.parametrize("name", ALGOS)
def test_retry_after_is_not_wildly_pessimistic(name):
    """Just before retry_after elapses the request must still be denied."""
    clock = Clock(1003.0)
    lim = make(name, clock)
    for _ in range(5):
        lim.hit("k")
    d = lim.hit("k")
    clock.t += d.retry_after - 0.01
    assert not lim.hit("k").allowed


@pytest.mark.parametrize("name", ALGOS)
def test_cost_larger_than_limit_never_allowed(name):
    d = make(name, Clock()).hit("k", cost=6)
    assert not d.allowed and d.retry_after is None


@pytest.mark.parametrize("name", ALGOS)
def test_weighted_cost(name):
    lim = make(name, Clock(1005.0))
    assert lim.hit("k", cost=3).allowed
    assert not lim.hit("k", cost=3).allowed
    assert lim.hit("k", cost=2).allowed


def test_invalid_cost_and_params():
    lim = make("token_bucket", Clock())
    with pytest.raises(ValueError):
        lim.hit("k", cost=0)
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        FixedWindow(1, 0)


def test_token_bucket_refills_and_caps_at_capacity():
    clock = Clock()
    lim = Limiter(TokenBucket(4, 1.0), clock=clock)
    for _ in range(4):
        assert lim.hit("k").allowed
    assert not lim.hit("k").allowed
    clock.t += 2
    assert lim.hit("k").remaining == 1  # 2 tokens refilled, 1 spent
    clock.t += 1000
    assert lim.hit("k").remaining == 3  # capped at capacity, not 1000


def test_token_bucket_allows_burst_then_steady_rate():
    clock = Clock()
    lim = Limiter(TokenBucket(3, 1.0), clock=clock)
    allowed = 0
    for _ in range(60):  # 60 requests over 30s at 2 req/s
        allowed += lim.hit("k").allowed
        clock.t += 0.5
    assert allowed == 3 + 29  # initial burst + 1 token/s for the remaining time


def test_fixed_window_boundary_burst_vs_sliding_log():
    """Fixed windows admit 2x the limit across a boundary; the exact log does not."""
    fixed = Limiter(FixedWindow(5, 10), clock=(c1 := Clock(1009.0)))
    exact = Limiter(SlidingWindowLog(5, 10), clock=(c2 := Clock(1009.0)))
    n_fixed = n_exact = 0
    for t in (1009.0, 1010.0):
        c1.t = c2.t = t
        for _ in range(5):
            n_fixed += fixed.hit("k").allowed
            n_exact += exact.hit("k").allowed
    assert n_fixed == 10
    assert n_exact == 5


def test_sliding_counter_weights_previous_window():
    clock = Clock(1000.0)  # window index boundary: 1000 / 10 is an integer
    lim = Limiter(SlidingWindowCounter(10, 10), clock=clock)
    for _ in range(10):
        assert lim.hit("k").allowed
    clock.t = 1015.0  # halfway: previous weighs 0.5 -> estimate 5
    allowed = sum(lim.hit("k").allowed for _ in range(10))
    assert allowed == 5


def test_sliding_counter_forgets_after_two_windows():
    clock = Clock(1000.0)
    lim = Limiter(SlidingWindowCounter(3, 10), clock=clock)
    for _ in range(3):
        lim.hit("k")
    clock.t = 1035.0
    assert lim.hit("k").remaining == 2


def test_build_algorithm():
    assert isinstance(build_algorithm({"type": "fixed_window", "limit": 1, "window": 1}), FixedWindow)
    with pytest.raises(ValueError, match="unknown"):
        build_algorithm({"type": "nope"})
    with pytest.raises(ValueError):
        build_algorithm({})
