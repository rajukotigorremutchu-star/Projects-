"""Micro-batch sessionization with watermarks, dedup, dead-lettering and checkpointing.

Semantics
- Sessions: per-user events whose consecutive gaps are <= ``gap_s`` seconds.
- Watermark: (max event_time seen in *previous* batches) - ``allowed_lateness_s``.
  Using only previous batches makes results independent of in-batch ordering.
- An event with event_time < watermark at arrival is *late* -> dead letter.
- An event_id seen before is a *duplicate* -> dead letter.
- A buffered session is finalized once its end + gap_s < watermark, i.e. no
  accepted event can still extend it.
- State and output commit in one SQLite transaction per batch, and batch ids
  are recorded, so re-running a batch is a no-op (idempotent).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS batches (batch_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS seen_events (event_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS buffer (
    event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, event_time INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (
    user_id TEXT NOT NULL, start_time INTEGER NOT NULL, end_time INTEGER NOT NULL,
    event_count INTEGER NOT NULL, duration_s INTEGER NOT NULL,
    PRIMARY KEY (user_id, start_time));
CREATE TABLE IF NOT EXISTS dead_letters (
    batch_id TEXT NOT NULL, event_id TEXT, reason TEXT NOT NULL, raw TEXT NOT NULL);
"""


@dataclass
class BatchResult:
    batch_id: str
    skipped: bool = False
    accepted: int = 0
    duplicates: int = 0
    late: int = 0
    invalid: int = 0
    sessions_finalized: int = 0
    watermark: int | None = None


@dataclass
class Sessionizer:
    db_path: str = ":memory:"
    gap_s: int = 1800
    allowed_lateness_s: int = 600
    conn: sqlite3.Connection = field(init=False)

    def __post_init__(self):
        self.conn = sqlite3.connect(self.db_path)
        self.conn.executescript(SCHEMA)

    # -- state helpers -------------------------------------------------
    def _get_max_time(self) -> int | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key='max_event_time'").fetchone()
        return int(row[0]) if row else None

    def watermark(self) -> int | None:
        m = self._get_max_time()
        return None if m is None else m - self.allowed_lateness_s

    # -- main entry ----------------------------------------------------
    def process_batch(self, batch_id: str, events: list[dict]) -> BatchResult:
        res = BatchResult(batch_id)
        c = self.conn
        if c.execute("SELECT 1 FROM batches WHERE batch_id=?", (batch_id,)).fetchone():
            res.skipped = True
            res.watermark = self.watermark()
            return res
        wm = self.watermark()
        max_time = self._get_max_time()
        with c:  # single transaction
            for ev in events:
                raw = json.dumps(ev, sort_keys=True, default=str)
                eid, uid, t = ev.get("event_id"), ev.get("user_id"), ev.get("event_time")
                if not eid or not uid or isinstance(t, bool) or not isinstance(t, int):
                    res.invalid += 1
                    c.execute("INSERT INTO dead_letters VALUES (?,?,?,?)", (batch_id, eid, "invalid", raw))
                    continue
                if c.execute("SELECT 1 FROM seen_events WHERE event_id=?", (eid,)).fetchone():
                    res.duplicates += 1
                    c.execute("INSERT INTO dead_letters VALUES (?,?,?,?)", (batch_id, eid, "duplicate", raw))
                    continue
                if wm is not None and t < wm:
                    res.late += 1
                    c.execute("INSERT INTO seen_events VALUES (?)", (eid,))
                    c.execute("INSERT INTO dead_letters VALUES (?,?,?,?)", (batch_id, eid, "late", raw))
                    continue
                c.execute("INSERT INTO seen_events VALUES (?)", (eid,))
                c.execute("INSERT INTO buffer VALUES (?,?,?)", (eid, uid, t))
                res.accepted += 1
                max_time = t if max_time is None else max(max_time, t)
            if max_time is not None:
                c.execute("INSERT OR REPLACE INTO meta VALUES ('max_event_time', ?)", (str(max_time),))
            res.watermark = None if max_time is None else max_time - self.allowed_lateness_s
            res.sessions_finalized = self._finalize(res.watermark)
            c.execute("INSERT INTO batches VALUES (?)", (batch_id,))
        return res

    def flush(self) -> int:
        """Finalize everything still buffered (end of stream)."""
        with self.conn:
            return self._finalize(None, force=True)

    def _finalize(self, wm: int | None, force: bool = False) -> int:
        rows = self.conn.execute(
            "SELECT event_id, user_id, event_time FROM buffer ORDER BY user_id, event_time, event_id"
        ).fetchall()
        by_user: dict[str, list[tuple[str, int]]] = {}
        for eid, uid, t in rows:
            by_user.setdefault(uid, []).append((eid, t))
        done = 0
        for uid, evs in by_user.items():
            groups, cur = [], [evs[0]]
            for e in evs[1:]:
                if e[1] - cur[-1][1] <= self.gap_s:
                    cur.append(e)
                else:
                    groups.append(cur)
                    cur = [e]
            groups.append(cur)
            for g in groups:
                start, end = g[0][1], g[-1][1]
                if force or (wm is not None and end + self.gap_s < wm):
                    self.conn.execute(
                        "INSERT INTO sessions VALUES (?,?,?,?,?)", (uid, start, end, len(g), end - start))
                    self.conn.executemany("DELETE FROM buffer WHERE event_id=?", [(e[0],) for e in g])
                    done += 1
        return done

    # -- reads ---------------------------------------------------------
    def sessions(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT user_id, start_time, end_time, event_count, duration_s FROM sessions "
            "ORDER BY user_id, start_time").fetchall()

    def dead_letters(self) -> list[tuple]:
        return self.conn.execute("SELECT batch_id, event_id, reason FROM dead_letters ORDER BY rowid").fetchall()
