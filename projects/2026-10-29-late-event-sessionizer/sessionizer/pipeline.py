"""Incremental sessionization of clickstream batches with late-data handling.

Batches arrive in order. For each batch we validate rows, drop duplicates,
divert events that are too late (older than watermark - allowed lateness),
and rebuild sessions only for users touched by the batch.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import duckdb

VALID_TYPES = ("page_view", "click", "add_to_cart", "purchase")


@dataclass
class BatchReport:
    batch_id: str
    rows_read: int = 0
    accepted: int = 0
    rejected_invalid: int = 0
    rejected_duplicate: int = 0
    late_diverted: int = 0
    users_recomputed: int = 0
    watermark: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class Sessionizer:
    def __init__(self, db_path: str = ":memory:", gap_minutes: int = 30,
                 allowed_lateness_minutes: int = 60):
        if gap_minutes <= 0 or allowed_lateness_minutes < 0:
            raise ValueError("gap_minutes must be > 0 and allowed_lateness_minutes >= 0")
        self.gap = gap_minutes
        self.lateness = allowed_lateness_minutes
        self.con = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS events(
                event_id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL,
                event_ts TIMESTAMP NOT NULL, event_type VARCHAR NOT NULL,
                batch_id VARCHAR NOT NULL);
            CREATE TABLE IF NOT EXISTS late_events(
                event_id VARCHAR, user_id VARCHAR, event_ts TIMESTAMP,
                event_type VARCHAR, batch_id VARCHAR, watermark TIMESTAMP);
            CREATE TABLE IF NOT EXISTS rejects(
                batch_id VARCHAR, event_id VARCHAR, user_id VARCHAR,
                event_ts VARCHAR, event_type VARCHAR, reason VARCHAR);
            CREATE TABLE IF NOT EXISTS sessions(
                session_id VARCHAR, user_id VARCHAR, start_ts TIMESTAMP,
                end_ts TIMESTAMP, event_count INTEGER, duration_s BIGINT,
                has_purchase BOOLEAN);
            CREATE TABLE IF NOT EXISTS processed_batches(batch_id VARCHAR PRIMARY KEY);
        """)

    # ------------------------------------------------------------------
    def watermark(self):
        return self.con.execute("SELECT max(event_ts) FROM events").fetchone()[0]

    def process_file(self, path: str | Path) -> BatchReport:
        path = Path(path)
        return self._process(path.stem, f"read_csv('{path.as_posix()}', header=true, "
                                        "all_varchar=true, strict_mode=false, null_padding=true)")

    def _process(self, batch_id: str, source_sql: str) -> BatchReport:
        c = self.con
        if c.execute("SELECT 1 FROM processed_batches WHERE batch_id=?", [batch_id]).fetchone():
            raise ValueError(f"batch {batch_id!r} already processed")
        rep = BatchReport(batch_id)
        wm = self.watermark()
        c.execute("BEGIN")
        try:
            c.execute(f"""
                CREATE OR REPLACE TEMP TABLE stg AS
                SELECT row_number() OVER () AS rn,
                       nullif(trim(event_id), '') AS event_id,
                       nullif(trim(user_id), '') AS user_id,
                       nullif(trim(event_ts), '') AS raw_ts,
                       try_cast(nullif(trim(event_ts), '') AS TIMESTAMP) AS event_ts,
                       nullif(trim(event_type), '') AS event_type
                FROM {source_sql}""")
            rep.rows_read = c.execute("SELECT count(*) FROM stg").fetchone()[0]

            types = ",".join(f"'{t}'" for t in VALID_TYPES)
            c.execute(f"""
                CREATE OR REPLACE TEMP TABLE classified AS
                SELECT *,
                  CASE WHEN event_id IS NULL THEN 'missing_event_id'
                       WHEN user_id IS NULL THEN 'missing_user_id'
                       WHEN event_ts IS NULL THEN 'bad_timestamp'
                       WHEN event_type IS NULL OR event_type NOT IN ({types}) THEN 'bad_event_type'
                  END AS invalid_reason,
                  row_number() OVER (PARTITION BY event_id ORDER BY rn) AS dup_rank
                FROM stg""")
            c.execute("""
                CREATE OR REPLACE TEMP TABLE known_ids AS
                SELECT event_id FROM events UNION SELECT event_id FROM late_events""")

            # rejects: invalid first, then duplicates of already-seen ids
            c.execute("""
                INSERT INTO rejects
                SELECT ?, event_id, user_id, raw_ts, event_type,
                       coalesce(invalid_reason, 'duplicate_event_id')
                FROM classified
                WHERE invalid_reason IS NOT NULL
                   OR dup_rank > 1
                   OR event_id IN (SELECT event_id FROM known_ids)""", [batch_id])
            rep.rejected_invalid = c.execute(
                "SELECT count(*) FROM classified WHERE invalid_reason IS NOT NULL").fetchone()[0]

            c.execute("""
                CREATE OR REPLACE TEMP TABLE good AS
                SELECT * FROM classified
                WHERE invalid_reason IS NULL AND dup_rank = 1
                  AND event_id NOT IN (SELECT event_id FROM known_ids)""")
            rep.rejected_duplicate = (rep.rows_read - rep.rejected_invalid
                                      - c.execute("SELECT count(*) FROM good").fetchone()[0])

            # late policy uses the watermark as of the START of the batch
            if wm is not None:
                c.execute("""
                    INSERT INTO late_events
                    SELECT event_id, user_id, event_ts, event_type, ?, ?
                    FROM good WHERE event_ts < ? - to_minutes(?)""",
                          [batch_id, wm, wm, self.lateness])
                rep.late_diverted = c.execute(
                    "SELECT count(*) FROM good WHERE event_ts < ? - to_minutes(?)",
                    [wm, self.lateness]).fetchone()[0]
                c.execute("DELETE FROM good WHERE event_ts < ? - to_minutes(?)",
                          [wm, self.lateness])

            c.execute("""INSERT INTO events
                         SELECT event_id, user_id, event_ts, event_type, ? FROM good""", [batch_id])
            rep.accepted = c.execute("SELECT count(*) FROM good").fetchone()[0]

            c.execute("CREATE OR REPLACE TEMP TABLE affected AS SELECT DISTINCT user_id FROM good")
            rep.users_recomputed = c.execute("SELECT count(*) FROM affected").fetchone()[0]
            self._rebuild_sessions("affected")
            c.execute("INSERT INTO processed_batches VALUES (?)", [batch_id])
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        wm = self.watermark()
        rep.watermark = str(wm) if wm else None
        return rep

    def _rebuild_sessions(self, users_table: str | None) -> None:
        """Recompute sessions for the users in `users_table` (None = everyone)."""
        c = self.con
        where = f"WHERE user_id IN (SELECT user_id FROM {users_table})" if users_table else ""
        c.execute(f"DELETE FROM sessions {where}")
        c.execute(f"""
            INSERT INTO sessions
            WITH ev AS (
                SELECT * FROM events {where}
            ), flagged AS (
                SELECT *, CASE WHEN lag(event_ts) OVER w IS NULL
                                 OR event_ts - lag(event_ts) OVER w > to_minutes(?)
                               THEN 1 ELSE 0 END AS new_sess
                FROM ev WINDOW w AS (PARTITION BY user_id ORDER BY event_ts, event_id)
            ), numbered AS (
                SELECT *, sum(new_sess) OVER (PARTITION BY user_id
                          ORDER BY event_ts, event_id) AS sess_no FROM flagged
            )
            SELECT user_id || '#' || strftime(min(event_ts), '%Y%m%dT%H%M%S'),
                   user_id, min(event_ts), max(event_ts), count(*)::INTEGER,
                   date_diff('second', min(event_ts), max(event_ts)),
                   bool_or(event_type = 'purchase')
            FROM numbered GROUP BY user_id, sess_no""", [self.gap])

    def full_rebuild(self) -> None:
        """Reference implementation: recompute all sessions from accepted events."""
        self._rebuild_sessions(None)

    # ------------------------------------------------------------------
    def sessions(self) -> list[tuple]:
        return self.con.execute(
            "SELECT * FROM sessions ORDER BY user_id, start_ts").fetchall()

    def counts(self) -> dict:
        return {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in ("events", "late_events", "rejects", "sessions")}

    def export_sessions_csv(self, out: str | Path) -> None:
        self.con.execute(
            f"COPY (SELECT * FROM sessions ORDER BY user_id, start_ts) TO '{Path(out).as_posix()}' "
            "(HEADER, DELIMITER ',')")
