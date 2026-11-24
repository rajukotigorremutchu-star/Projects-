"""State stores. ``update`` must apply read-modify-write atomically."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Callable, Protocol

# fn(state | None) -> (result, new_state, ttl_seconds)
UpdateFn = Callable[[Any], "tuple[Any, Any, float]"]


class Store(Protocol):
    def update(self, key: str, fn: UpdateFn, now: float) -> Any: ...


class MemoryStore:
    """Thread-safe in-process store with lazy TTL expiry."""

    def __init__(self, sweep_every: int = 1000):
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._sweep_every = sweep_every
        self._ops = 0

    def update(self, key, fn, now):
        with self._lock:
            entry = self._data.get(key)
            state = entry[0] if entry and entry[1] > now else None
            result, new_state, ttl = fn(state)
            self._data[key] = (new_state, now + ttl)
            self._ops += 1
            if self._ops % self._sweep_every == 0:
                self._data = {k: v for k, v in self._data.items() if v[1] > now}
            return result

    def __len__(self):
        return len(self._data)


class SQLiteStore:
    """Store backed by SQLite so several processes/connections share limits.

    Each update runs in a ``BEGIN IMMEDIATE`` transaction, which takes the
    write lock up front and makes the read-modify-write atomic across
    connections.
    """

    def __init__(self, path: str, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout
        self._local = threading.local()
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL") if path != ":memory:" else None
            c.execute(
                "CREATE TABLE IF NOT EXISTS ratelimit_state ("
                "key TEXT PRIMARY KEY, state TEXT NOT NULL, expires_at REAL NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
            self._local.conn = conn
        return conn

    def update(self, key, fn, now):
        c = self._conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute(
                "SELECT state, expires_at FROM ratelimit_state WHERE key = ?", (key,)
            ).fetchone()
            state = json.loads(row[0]) if row and row[1] > now else None
            result, new_state, ttl = fn(state)
            c.execute(
                "INSERT INTO ratelimit_state (key, state, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
                "expires_at = excluded.expires_at",
                (key, json.dumps(new_state), now + ttl),
            )
            c.execute("COMMIT")
            return result
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def purge_expired(self, now: float) -> int:
        c = self._conn()
        return c.execute("DELETE FROM ratelimit_state WHERE expires_at <= ?", (now,)).rowcount
