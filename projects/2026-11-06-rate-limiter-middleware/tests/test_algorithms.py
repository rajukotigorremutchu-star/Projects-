import pytest
from ratelimiter import FixedWindow, SlidingWindowCounter, SlidingWindowLog, TokenBucket, make_limiter


class Clock:
    def __init__(self, t=0.0): self.t = t
    def __call__(self): return self.t


ALL = [FixedWindow, SlidingWindowLog, SlidingWindowCounter, TokenBucket]


@pytest.mark.parametrize("cls", ALL)
def test_limit_enforced_and_recovers(cls):
    c = Clock(100.0)
    lim = cls(3, 10, clock=c)
    assert [lim.check("k").allowed for _ in range(4)] == [True, True, True, False]
    c.t += 20.5  # two windows: the weighted counter needs the previous window fully aged out
    assert lim.check("k").allowed


@pytest.mark.parametrize("cls", ALL)
def test_keys_are_independent(cls):
    lim = cls(1, 10, clock=Clock(0.0))
    assert lim.check("a").allowed and lim.check("b").allowed
    assert not lim.check("a").allowed


@pytest.mark.parametrize("cls", ALL)
def test_remaining_counts_down(cls):
    lim = cls(3, 10, clock=Clock(0.0))
    assert [lim.check("k").remaining for _ in range(3)] == [2, 1, 0]


@pytest.mark.parametrize("cls", ALL)
def test_invalid_args(cls):
    with pytest.raises(ValueError):
        cls(0, 10)
    with pytest.raises(ValueError):
        cls(1, 10).check("k", cost=0)


def test_fixed_window_boundary_burst():
    c = Clock(9.5)
    lim = FixedWindow(5, 10, clock=c)
    assert sum(lim.check("k").allowed for _ in range(5)) == 5
    c.t = 10.0
    assert sum(lim.check("k").allowed for _ in range(5)) == 5  # 10 in 0.5s: the known flaw


def test_sliding_log_has_no_boundary_burst():
    c = Clock(9.5)
    lim = SlidingWindowLog(5, 10, clock=c)
    assert sum(lim.check("k").allowed for _ in range(5)) == 5
    c.t = 10.0
    assert not lim.check("k").allowed


def test_sliding_log_retry_after_exact():
    c = Clock(0.0)
    lim = SlidingWindowLog(2, 10, clock=c)
    lim.check("k"); c.t = 3; lim.check("k"); c.t = 4
    d = lim.check("k")
    assert not d.allowed and d.retry_after == pytest.approx(6.0)  # oldest expires at t=10
    c.t = 10.0
    assert lim.check("k").allowed


def test_sliding_counter_blocks_boundary_burst():
    c = Clock(9.5)
    lim = SlidingWindowCounter(5, 10, clock=c)
    for _ in range(5):
        assert lim.check("k").allowed
    c.t = 10.0  # previous window weight 0.95 -> estimate 4.75, only 0 more whole units
    assert not lim.check("k").allowed


def test_token_bucket_burst_then_refill():
    c = Clock(0.0)
    lim = TokenBucket(10, 10, clock=c)  # 1 token/s
    assert all(lim.check("k").allowed for _ in range(10))
    d = lim.check("k")
    assert not d.allowed and d.retry_after == pytest.approx(1.0)
    c.t = 2.0
    assert lim.check("k", cost=2).allowed
    assert not lim.check("k").allowed


def test_token_bucket_cost_above_capacity_never_allowed():
    d = TokenBucket(2, 10, clock=Clock(0.0)).check("k", cost=5)
    assert not d.allowed and d.retry_after == float("inf")


def test_make_limiter_unknown():
    with pytest.raises(ValueError):
        make_limiter("nope", 1, 1)
