import math
import threading

import pytest

from ratelimit import ManualClock, make_limiter

ALL = ["token_bucket", "fixed_window", "sliding_log", "sliding_counter"]


@pytest.mark.parametrize("name", ALL)
def test_allows_up_to_limit_then_denies(name):
    c = ManualClock(0.5)
    lim = make_limiter(name, 3, 10, c)
    assert [lim.check("k").allowed for _ in range(4)] == [True, True, True, False]


@pytest.mark.parametrize("name", ALL)
def test_keys_are_independent(name):
    lim = make_limiter(name, 1, 10, ManualClock(0.5))
    assert lim.check("a").allowed and lim.check("b").allowed
    assert not lim.check("a").allowed


@pytest.mark.parametrize("name", ALL)
def test_recovers_after_two_windows(name):
    c = ManualClock(0.5)
    lim = make_limiter(name, 2, 10, c)
    lim.check("k"); lim.check("k")
    assert not lim.check("k").allowed
    c.advance(20)
    assert lim.check("k").allowed


@pytest.mark.parametrize("name", ALL)
def test_remaining_decrements(name):
    lim = make_limiter(name, 5, 10, ManualClock(0.5))
    assert lim.check("k").remaining == 4
    assert lim.check("k", cost=2).remaining == 2


@pytest.mark.parametrize("name", ALL)
def test_denied_does_not_consume(name):
    c = ManualClock(0.5)
    lim = make_limiter(name, 2, 10, c)
    lim.check("k", 2)
    for _ in range(5):
        assert not lim.check("k").allowed
    c.advance(20)
    assert [lim.check("k").allowed for _ in range(3)] == [True, True, False]


@pytest.mark.parametrize("name", ALL)
def test_invalid_args(name):
    with pytest.raises(ValueError):
        make_limiter(name, 0, 1)
    with pytest.raises(ValueError):
        make_limiter(name, 1, 0)
    with pytest.raises(ValueError):
        make_limiter(name, 1, 1).check("k", 0)


def test_unknown_algorithm():
    with pytest.raises(ValueError, match="unknown algorithm"):
        make_limiter("nope", 1, 1)


def test_cost_above_limit_never_allowed():
    for name in ALL:
        d = make_limiter(name, 3, 10, ManualClock(0.5)).check("k", 4)
        assert not d.allowed


def test_fixed_window_boundary_burst_allows_double():
    c = ManualClock(9.9)
    lim = make_limiter("fixed_window", 10, 10, c)
    assert all(lim.check("k").allowed for _ in range(10))
    c.t = 10.1
    assert all(lim.check("k").allowed for _ in range(10))  # known weakness


def test_sliding_log_exact_no_boundary_burst():
    c = ManualClock(9.9)
    lim = make_limiter("sliding_log", 10, 10, c)
    for _ in range(10):
        lim.check("k")
    c.t = 10.1
    assert not lim.check("k").allowed
    c.t = 19.95  # first entries (t=9.9) have expired
    assert lim.check("k").allowed


def test_sliding_log_retry_after_exact():
    c = ManualClock(0)
    lim = make_limiter("sliding_log", 2, 10, c)
    lim.check("k"); c.t = 4; lim.check("k"); c.t = 5
    d = lim.check("k")
    assert not d.allowed and d.retry_after == pytest.approx(5.0)  # oldest (t=0) expires at 10


def test_token_bucket_refill_and_retry_after():
    c = ManualClock(0)
    lim = make_limiter("token_bucket", 10, 10, c)  # 1 token/s
    lim.check("k", 10)
    d = lim.check("k")
    assert not d.allowed and d.retry_after == pytest.approx(1.0)
    c.advance(1)
    assert lim.check("k").allowed
    assert not lim.check("k").allowed


def test_token_bucket_does_not_overfill():
    c = ManualClock(0)
    lim = make_limiter("token_bucket", 3, 3, c)
    lim.check("k"); c.advance(1000)
    assert lim.check("k").remaining == 2


def test_fixed_window_retry_after_is_window_end():
    c = ManualClock(2.0)
    lim = make_limiter("fixed_window", 1, 10, c)
    lim.check("k")
    assert lim.check("k").retry_after == pytest.approx(8.0)


def test_sliding_counter_weights_previous_window():
    c = ManualClock(5.0)
    lim = make_limiter("sliding_counter", 10, 10, c)
    for _ in range(10):
        assert lim.check("k").allowed
    c.t = 12.0  # 20% into new window: est = 10*0.8 = 8 -> 2 more allowed
    assert [lim.check("k").allowed for _ in range(3)] == [True, True, False]


def test_sliding_counter_retry_hint_is_not_after_window_end():
    c = ManualClock(5.0)
    lim = make_limiter("sliding_counter", 10, 10, c)
    for _ in range(10):
        lim.check("k")
    d = lim.check("k")
    assert not d.allowed and 0 < d.retry_after <= d.reset_after


def test_eviction_bounds_memory_and_prefers_idle():
    c = ManualClock(0)
    lim = make_limiter("fixed_window", 5, 10, c, max_keys=3)
    for k in "abc":
        lim.check(k)
    c.t = 25  # all stale
    lim.check("d")
    assert set(lim._state) == {"d"}
    for k in "efgh":
        lim.check(k)
    assert len(lim._state) <= 3


def test_thread_safety_never_exceeds_limit():
    lim = make_limiter("sliding_log", 100, 1000, ManualClock(0))
    ok = []

    def work():
        for _ in range(100):
            ok.append(lim.check("k").allowed)

    ts = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sum(ok) == 100


def test_decision_infinite_retry_for_impossible_cost():
    assert math.isinf(make_limiter("token_bucket", 3, 10, ManualClock(0)).check("k", 9).retry_after)
