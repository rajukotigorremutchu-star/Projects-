"""SQLite persistence for endpoints, events, deliveries and delivery attempts."""
import fnmatch
import json
import sqlite3
import threading
import uuid
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    secret TEXT NOT NULL,
    event_types TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    body TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    status TEXT NOT NULL,            -- pending | in_flight | succeeded | dead
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL,
    last_status_code INTEGER,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deliveries_due ON deliveries(status, next_attempt_at);
CREATE TABLE IF NOT EXISTS attempts (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    n INTEGER NOT NULL,
    at REAL NOT NULL,
    status_code INTEGER,
    error TEXT
);
"""


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Store:
    """Thread-safe (single connection + lock) SQLite store."""

    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            # A crash mid-send leaves rows in_flight; they were never confirmed, so retry them.
            self._conn.execute("UPDATE deliveries SET status='pending' WHERE status='in_flight'")
            self._conn.commit()

    # -- endpoints ---------------------------------------------------------
    def add_endpoint(self, url: str, secret: str, event_types: list[str], now: float) -> dict:
        eid = new_id("ep")
        with self._lock:
            self._conn.execute(
                "INSERT INTO endpoints VALUES (?,?,?,?,1,?)",
                (eid, url, secret, json.dumps(event_types), now),
            )
            self._conn.commit()
        return self.get_endpoint(eid)

    def get_endpoint(self, eid: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM endpoints WHERE id=?", (eid,)).fetchone()
        return self._endpoint(row) if row else None

    def list_endpoints(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM endpoints ORDER BY created_at, id").fetchall()
        return [self._endpoint(r) for r in rows]

    def set_endpoint_active(self, eid: str, active: bool) -> Optional[dict]:
        with self._lock:
            self._conn.execute("UPDATE endpoints SET active=? WHERE id=?", (int(active), eid))
            self._conn.commit()
        return self.get_endpoint(eid)

    @staticmethod
    def _endpoint(row) -> dict:
        d = dict(row)
        d["event_types"] = json.loads(d["event_types"])
        d["active"] = bool(d["active"])
        return d

    # -- events & deliveries -------------------------------------------------
    def create_event(
        self, event_id: str, event_type: str, body: str, idempotency_key: Optional[str], now: float
    ) -> tuple[dict, bool]:
        """Insert an event and fan out deliveries. Returns (event, created).

        If `idempotency_key` was seen before, the existing event is returned and nothing
        is fanned out again.
        """
        with self._lock:
            if idempotency_key:
                row = self._conn.execute(
                    "SELECT * FROM events WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                if row:
                    return dict(row), False
            self._conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?)",
                (event_id, event_type, body, idempotency_key, now),
            )
            for ep in self._conn.execute("SELECT * FROM endpoints WHERE active=1").fetchall():
                types = json.loads(ep["event_types"])
                if any(fnmatch.fnmatchcase(event_type, pattern) for pattern in types):
                    self._conn.execute(
                        "INSERT INTO deliveries (id,event_id,endpoint_id,status,attempts,"
                        "next_attempt_at,created_at,updated_at) VALUES (?,?,?,?,0,?,?,?)",
                        (new_id("dlv"), event_id, ep["id"], "pending", now, now, now),
                    )
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row), True

    def claim_due(self, now: float, limit: int) -> list[dict]:
        """Atomically move due pending deliveries to in_flight and return them with context."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT d.*, e.body AS body, e.type AS event_type, p.url AS url, p.secret AS secret,"
                " p.active AS endpoint_active FROM deliveries d"
                " JOIN events e ON e.id=d.event_id JOIN endpoints p ON p.id=d.endpoint_id"
                " WHERE d.status='pending' AND d.next_attempt_at<=?"
                " ORDER BY d.next_attempt_at, d.id LIMIT ?",
                (now, limit),
            ).fetchall()
            for r in rows:
                self._conn.execute(
                    "UPDATE deliveries SET status='in_flight', updated_at=? WHERE id=?",
                    (now, r["id"]),
                )
            self._conn.commit()
        return [dict(r) for r in rows]

    def record_attempt(
        self,
        delivery_id: str,
        now: float,
        status_code: Optional[int],
        error: Optional[str],
        new_status: str,
        next_attempt_at: Optional[float] = None,
    ) -> None:
        with self._lock:
            n = (
                self._conn.execute(
                    "SELECT attempts FROM deliveries WHERE id=?", (delivery_id,)
                ).fetchone()["attempts"]
                + 1
            )
            self._conn.execute(
                "INSERT INTO attempts (delivery_id,n,at,status_code,error) VALUES (?,?,?,?,?)",
                (delivery_id, n, now, status_code, error),
            )
            self._conn.execute(
                "UPDATE deliveries SET status=?, attempts=?, last_status_code=?, last_error=?,"
                " next_attempt_at=COALESCE(?, next_attempt_at), updated_at=? WHERE id=?",
                (new_status, n, status_code, error, next_attempt_at, now, delivery_id),
            )
            self._conn.commit()

    def requeue(self, delivery_id: str, now: float) -> Optional[dict]:
        """Manually replay a dead delivery. Attempt history is kept; the count restarts."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE deliveries SET status='pending', attempts=0, next_attempt_at=?, updated_at=?"
                " WHERE id=? AND status='dead'",
                (now, now, delivery_id),
            )
            if cur.rowcount == 0:
                return None
            self._conn.commit()
        return self.get_delivery(delivery_id)

    def get_delivery(self, delivery_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM deliveries WHERE id=?", (delivery_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["attempt_log"] = [
                dict(a)
                for a in self._conn.execute(
                    "SELECT n, at, status_code, error FROM attempts WHERE delivery_id=?"
                    " ORDER BY seq",
                    (delivery_id,),
                ).fetchall()
            ]
        return d

    def list_deliveries(self, status: Optional[str] = None, limit: int = 100) -> list[dict]:
        q, args = "SELECT * FROM deliveries", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY created_at, id LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def get_event(self, event_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None
