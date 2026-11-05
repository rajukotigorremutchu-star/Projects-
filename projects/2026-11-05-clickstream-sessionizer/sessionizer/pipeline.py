"""Incremental clickstream pipeline.

Raw JSONL batch -> validate (quarantine bad rows) -> dedupe by event_id ->
upsert into `events` -> recompute sessions ONLY for users touched by the batch
-> refresh daily metrics -> optionally export Parquet.

Re-running the same batch is a no-op (idempotent); late-arriving events
correctly merge into / split existing sessions because affected users are
fully re-sessionized from their stored events.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import duckdb

VALID_TYPES = {"page_view", "click", "add_to_cart", "purchase"}
REQUIRED = ("event_id", "user_id", "event_type", "ts")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    event_type VARCHAR NOT NULL,
    ts TIMESTAMP NOT NULL,
    url VARCHAR,
    batch VARCHAR
);
CREATE TABLE IF NOT EXISTS quarantine (
    batch VARCHAR, line_no INTEGER, reason VARCHAR, raw VARCHAR
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR, user_id VARCHAR, session_start TIMESTAMP,
    session_end TIMESTAMP, duration_s BIGINT, n_events BIGINT,
    n_page_views BIGINT, converted BOOLEAN, session_date DATE
);
CREATE TABLE IF NOT EXISTS daily_metrics (
    session_date DATE, sessions BIGINT, users BIGINT,
    avg_duration_s DOUBLE, conversion_rate DOUBLE
);
CREATE TABLE IF NOT EXISTS processed_batches (
    batch VARCHAR PRIMARY KEY, processed_at TIMESTAMP, loaded BIGINT,
    duplicates BIGINT, quarantined BIGINT
);
"""


@dataclass
class BatchResult:
    batch: str
    read: int = 0
    loaded: int = 0
    duplicates: int = 0
    quarantined: int = 0
    skipped: bool = False
    affected_users: int = 0


def connect(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute(SCHEMA)
    return con


def parse_ts(value) -> datetime:
    """Parse ISO-8601 into a naive UTC datetime. Raises ValueError."""
    if not isinstance(value, str):
        raise ValueError("ts must be a string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def validate(raw_line: str) -> tuple[dict | None, str | None]:
    """Return (record, None) or (None, reason)."""
    try:
        rec = json.loads(raw_line)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(rec, dict):
        return None, "not_an_object"
    for f in REQUIRED:
        if rec.get(f) in (None, ""):
            return None, f"missing_{f}"
    if rec["event_type"] not in VALID_TYPES:
        return None, "unknown_event_type"
    try:
        rec["ts"] = parse_ts(rec["ts"])
    except (ValueError, TypeError):
        return None, "bad_timestamp"
    rec["event_id"] = str(rec["event_id"])
    rec["user_id"] = str(rec["user_id"])
    return rec, None


def ingest_batch(con, path: Path, gap_minutes: int = 30, force: bool = False) -> BatchResult:
    path = Path(path)
    batch = path.name
    res = BatchResult(batch=batch)
    if not force and con.execute(
        "SELECT 1 FROM processed_batches WHERE batch = ?", [batch]
    ).fetchone():
        res.skipped = True
        return res

    good: list[dict] = []
    bad: list[tuple] = []
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.strip():
                continue
            res.read += 1
            rec, reason = validate(line)
            if reason:
                bad.append((batch, i, reason, line.strip()[:500]))
            else:
                good.append(rec)

    # Dedupe within batch (first occurrence wins), then against stored events.
    seen: dict[str, dict] = {}
    for r in good:
        seen.setdefault(r["event_id"], r)
    in_batch_dups = len(good) - len(seen)

    con.execute("BEGIN")
    try:
        con.execute("DELETE FROM quarantine WHERE batch = ?", [batch])
        if bad:
            con.executemany("INSERT INTO quarantine VALUES (?,?,?,?)", bad)
        before = con.execute("SELECT count(*) FROM events").fetchone()[0]
        rows = [
            (r["event_id"], r["user_id"], r["event_type"], r["ts"], r.get("url"), batch)
            for r in seen.values()
        ]
        if rows:
            con.executemany(
                "INSERT INTO events VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING", rows
            )
        after = con.execute("SELECT count(*) FROM events").fetchone()[0]
        res.loaded = after - before
        res.duplicates = in_batch_dups + (len(seen) - res.loaded)
        res.quarantined = len(bad)

        users = sorted({r["user_id"] for r in seen.values()})
        res.affected_users = len(users)
        if users:
            resessionize(con, users, gap_minutes)
        refresh_daily_metrics(con)
        con.execute(
            "INSERT OR REPLACE INTO processed_batches VALUES (?, now()::TIMESTAMP, ?, ?, ?)",
            [batch, res.loaded, res.duplicates, res.quarantined],
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return res


def resessionize(con, users: list[str], gap_minutes: int) -> None:
    """Rebuild sessions for the given users from their full event history."""
    con.execute("CREATE OR REPLACE TEMP TABLE _affected (user_id VARCHAR)")
    con.executemany("INSERT INTO _affected VALUES (?)", [(u,) for u in users])
    con.execute("DELETE FROM sessions WHERE user_id IN (SELECT user_id FROM _affected)")
    con.execute(
        f"""
        INSERT INTO sessions
        WITH e AS (
            SELECT ev.* FROM events ev JOIN _affected a USING (user_id)
        ), flagged AS (
            SELECT *, CASE WHEN lag(ts) OVER w IS NULL
                           OR ts - lag(ts) OVER w > INTERVAL {int(gap_minutes)} MINUTE
                      THEN 1 ELSE 0 END AS new_session
            FROM e WINDOW w AS (PARTITION BY user_id ORDER BY ts, event_id)
        ), numbered AS (
            SELECT *, sum(new_session) OVER (
                PARTITION BY user_id ORDER BY ts, event_id
            ) AS seq FROM flagged
        )
        SELECT user_id || '-' || strftime(min(ts), '%Y%m%dT%H%M%S') AS session_id,
               user_id, min(ts), max(ts),
               date_diff('second', min(ts), max(ts)),
               count(*),
               count(*) FILTER (event_type = 'page_view'),
               bool_or(event_type = 'purchase'),
               CAST(min(ts) AS DATE)
        FROM numbered GROUP BY user_id, seq
        """
    )
    con.execute("DROP TABLE _affected")


def refresh_daily_metrics(con) -> None:
    con.execute("DELETE FROM daily_metrics")
    con.execute(
        """
        INSERT INTO daily_metrics
        SELECT session_date, count(*), count(DISTINCT user_id),
               round(avg(duration_s), 2),
               round(avg(CASE WHEN converted THEN 1.0 ELSE 0.0 END), 4)
        FROM sessions GROUP BY session_date ORDER BY session_date
        """
    )


def export_parquet(con, out_dir: Path) -> list[Path]:
    """Write sessions partitioned by session_date (Hive layout)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM sessions ORDER BY session_start) TO '{out_dir.as_posix()}' "
        "(FORMAT PARQUET, PARTITION_BY (session_date), OVERWRITE_OR_IGNORE)"
    )
    return sorted(out_dir.rglob("*.parquet"))
