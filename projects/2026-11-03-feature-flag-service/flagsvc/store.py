"""SQLite persistence with optimistic versioning and an audit history."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .models import Flag, FlagDefinition


class VersionConflict(Exception):
    pass


class FlagStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS flags (
                    key TEXT PRIMARY KEY, version INTEGER NOT NULL, definition TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL,
                    version INTEGER NOT NULL, action TEXT NOT NULL, definition TEXT);
                """)

    def put(self, key: str, definition: FlagDefinition,
            expected_version: Optional[int] = None) -> int:
        """Create/replace a flag. ``expected_version`` (0 = must not exist) guards
        against lost updates. Returns the new version."""
        Flag(key=key, **definition.model_dump())  # validates key
        body = definition.model_dump_json()
        with self._lock, self._conn:
            row = self._conn.execute("SELECT version FROM flags WHERE key=?", (key,)).fetchone()
            current = row[0] if row else 0
            if expected_version is not None and expected_version != current:
                raise VersionConflict(f"expected version {expected_version}, current {current}")
            new = current + 1
            self._conn.execute(
                "INSERT INTO flags(key,version,definition) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET version=excluded.version, definition=excluded.definition",
                (key, new, body))
            self._conn.execute(
                "INSERT INTO history(key,version,action,definition) VALUES(?,?,?,?)",
                (key, new, "create" if current == 0 else "update", body))
            return new

    def get(self, key: str) -> Optional[tuple[Flag, int]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT version, definition FROM flags WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        return Flag(key=key, **json.loads(row[1])), row[0]

    def list(self) -> list[tuple[Flag, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, version, definition FROM flags ORDER BY key").fetchall()
        return [(Flag(key=k, **json.loads(d)), v) for k, v, d in rows]

    def delete(self, key: str) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT version FROM flags WHERE key=?", (key,)).fetchone()
            if not row:
                return False
            self._conn.execute("DELETE FROM flags WHERE key=?", (key,))
            self._conn.execute(
                "INSERT INTO history(key,version,action,definition) VALUES(?,?,?,NULL)",
                (key, row[0] + 1, "delete"))
            return True

    def history(self, key: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT version, action, definition FROM history WHERE key=? ORDER BY id",
                (key,)).fetchall()
        return [{"version": v, "action": a, "definition": json.loads(d) if d else None}
                for v, a, d in rows]
