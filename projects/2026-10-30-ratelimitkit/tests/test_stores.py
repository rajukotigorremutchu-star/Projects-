import threading

import pytest

from ratelimitkit import Limiter, MemoryStore, SQLiteStore, TokenBucket, FixedWindow


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    return MemoryStore() if request.param == "memory" else SQLiteStore(str(tmp_path / "rl.db"))


def test_state_expires(store):
    clock = Clock()
    lim = Limiter(FixedWindow(1, 10), store, clock)
    assert lim.hit("k").allowed
    assert not lim.hit("k").allowed
    clock.t += 25  # past ttl -> state dropped, fresh start
    assert lim.hit("k").allowed


def test_concurrent_hits_never_exceed_limit(store):
    """16 threads x 10 hits against a limit of 20: exactly 20 must be allowed."""
    clock = Clock()
    lim = Limiter(FixedWindow(20, 3600), store, clock)
    results, lock = [], threading.Lock()

    def worker():
        local = [lim.hit("shared").allowed for _ in range(10)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 160
    assert sum(results) == 20


def test_sqlite_shared_between_separate_store_instances(tmp_path):
    """Two stores on the same file behave like two worker processes."""
    path = str(tmp_path / "shared.db")
    clock = Clock()
    a = Limiter(TokenBucket(3, 0.001), SQLiteStore(path), clock, name="x")
    b = Limiter(TokenBucket(3, 0.001), SQLiteStore(path), clock, name="x")
    assert [a.hit("k").allowed, b.hit("k").allowed, a.hit("k").allowed] == [True] * 3
    assert not b.hit("k").allowed


def test_sqlite_persists_across_reopen(tmp_path):
    path = str(tmp_path / "p.db")
    clock = Clock()
    Limiter(FixedWindow(1, 3600), SQLiteStore(path), clock).hit("k")
    assert not Limiter(FixedWindow(1, 3600), SQLiteStore(path), clock).hit("k").allowed


def test_sqlite_purge_expired(tmp_path):
    s = SQLiteStore(str(tmp_path / "g.db"))
    clock = Clock()
    Limiter(FixedWindow(1, 10), s, clock).hit("k")
    assert s.purge_expired(clock.t) == 0
    assert s.purge_expired(clock.t + 11) == 1


def test_sqlite_rolls_back_on_error(tmp_path):
    s = SQLiteStore(str(tmp_path / "r.db"))

    def boom(state):
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        s.update("k", boom, 1000.0)
    # connection must be usable (transaction closed) afterwards
    assert s.update("k", lambda st: (st, [1], 10), 1000.0) is None


def test_memory_store_sweeps_expired_keys():
    s = MemoryStore(sweep_every=5)
    for i in range(5):
        s.update(f"k{i}", lambda st: (None, [1], 1), 1000.0)
    assert len(s) == 5
    for i in range(5):
        s.update("new", lambda st: (None, [1], 1), 2000.0)
    assert len(s) == 1
