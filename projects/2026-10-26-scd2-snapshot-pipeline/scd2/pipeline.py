"""Incremental SCD Type 2 dimension builder over daily full snapshots, in DuckDB SQL."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import duckdb

TRACKED = ("name", "city", "plan")
OPEN_END = date(9999, 12, 31)

DDL = """
CREATE TABLE IF NOT EXISTS dim_customer (
    sk BIGINT PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    name VARCHAR, city VARCHAR, plan VARCHAR,
    row_hash VARCHAR NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE NOT NULL,
    is_current BOOLEAN NOT NULL
);
CREATE TABLE IF NOT EXISTS load_log (
    snapshot_date DATE PRIMARY KEY, rows_in INTEGER,
    inserted INTEGER, closed INTEGER
);
CREATE SEQUENCE IF NOT EXISTS sk_seq START 1;
"""


def _enc(col: str) -> str:
    """Length-prefixed, NULL-aware encoding so NULL, '' and '<null>' never collide."""
    return f"CASE WHEN {col} IS NULL THEN 'N' ELSE 'V' || length({col}) || ':' || {col} END"


class SnapshotError(ValueError):
    pass


def connect(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute(DDL)
    return con


def read_snapshot(path: Path) -> list[tuple]:
    """Read a snapshot CSV (customer_id,name,city,plan); validate keys."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    seen = set()
    out = []
    for r in rows:
        try:
            cid = int(r["customer_id"])
        except (KeyError, ValueError) as e:
            raise SnapshotError(f"{path}: bad customer_id in {r}") from e
        if cid in seen:
            raise SnapshotError(f"{path}: duplicate customer_id {cid}")
        seen.add(cid)
        out.append((cid, *[(r.get(c) or None) for c in TRACKED]))
    return out


def load_snapshot(con: duckdb.DuckDBPyConnection, snap_date: date, rows: list[tuple]) -> dict:
    """Apply one snapshot. Snapshots must arrive in strictly increasing date order.

    Re-loading an already-loaded date is a no-op (idempotent); loading a date
    older than the latest loaded one raises, since SCD2 history can't be
    rewritten incrementally.
    """
    last = con.execute("SELECT max(snapshot_date) FROM load_log").fetchone()[0]
    if last is not None:
        if snap_date == last:
            return {"skipped": True, "inserted": 0, "closed": 0}
        if snap_date < last:
            raise SnapshotError(f"out-of-order snapshot {snap_date} < {last}")
    con.execute("BEGIN")
    try:
        con.execute("CREATE OR REPLACE TEMP TABLE stg (customer_id INTEGER, name VARCHAR, city VARCHAR, plan VARCHAR)")
        if rows:
            con.executemany("INSERT INTO stg VALUES (?,?,?,?)", rows)
        con.execute(
            "CREATE OR REPLACE TEMP TABLE stg_h AS SELECT *, "
            "md5(concat_ws('|', " + ", ".join(_enc(c) for c in TRACKED) + ")) AS row_hash FROM stg"
        )
        # Close current rows that changed or disappeared from the snapshot.
        closed = con.execute(
            """UPDATE dim_customer SET valid_to = ?, is_current = FALSE
               WHERE is_current AND NOT EXISTS (
                   SELECT 1 FROM stg_h s WHERE s.customer_id = dim_customer.customer_id
                                           AND s.row_hash = dim_customer.row_hash)
               RETURNING 1""",
            [snap_date],
        ).fetchall()
        # Insert rows for new keys, changed rows, and re-appearing keys.
        inserted = con.execute(
            f"""INSERT INTO dim_customer
                SELECT nextval('sk_seq'), s.customer_id, s.name, s.city, s.plan, s.row_hash,
                       ?, DATE '{OPEN_END}', TRUE
                FROM stg_h s
                WHERE NOT EXISTS (SELECT 1 FROM dim_customer d
                                  WHERE d.is_current AND d.customer_id = s.customer_id)
                ORDER BY s.customer_id
                RETURNING 1""",
            [snap_date],
        ).fetchall()
        con.execute("INSERT INTO load_log VALUES (?,?,?,?)", [snap_date, len(rows), len(inserted), len(closed)])
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"skipped": False, "inserted": len(inserted), "closed": len(closed)}


def quality_checks(con: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Return {check_name: violation_count}; all zeros means healthy."""
    q = lambda sql: con.execute(sql).fetchone()[0]
    return {
        "multiple_current_rows_per_key": q(
            "SELECT count(*) FROM (SELECT customer_id FROM dim_customer WHERE is_current "
            "GROUP BY 1 HAVING count(*) > 1)"),
        "overlapping_intervals": q(
            "SELECT count(*) FROM dim_customer a JOIN dim_customer b "
            "ON a.customer_id = b.customer_id AND a.sk < b.sk "
            "AND a.valid_from < b.valid_to AND b.valid_from < a.valid_to"),
        "empty_or_inverted_intervals": q("SELECT count(*) FROM dim_customer WHERE valid_from >= valid_to"),
        "current_flag_mismatch": q(
            f"SELECT count(*) FROM dim_customer WHERE is_current <> (valid_to = DATE '{OPEN_END}')"),
        "adjacent_identical_versions": q(
            "SELECT count(*) FROM dim_customer a JOIN dim_customer b "
            "ON a.customer_id = b.customer_id AND a.valid_to = b.valid_from AND a.row_hash = b.row_hash"),
    }


def as_of(con: duckdb.DuckDBPyConnection, when: date) -> list[tuple]:
    """Point-in-time view of the dimension."""
    return con.execute(
        "SELECT customer_id, name, city, plan FROM dim_customer "
        "WHERE valid_from <= ? AND ? < valid_to ORDER BY customer_id", [when, when]).fetchall()


def export_dimension(con: duckdb.DuckDBPyConnection, out: Path) -> None:
    """Write the dimension to CSV in a deterministic order (surrogate key excluded)."""
    con.execute(
        "COPY (SELECT customer_id, name, city, plan, valid_from, "
        "CASE WHEN is_current THEN NULL ELSE valid_to END AS valid_to, is_current "
        "FROM dim_customer ORDER BY customer_id, valid_from) "
        f"TO '{out}' (HEADER, DELIMITER ',')")


def run_directory(con, snap_dir: Path) -> list[dict]:
    """Load every snapshot_YYYY-MM-DD.csv in a directory in date order."""
    results = []
    for p in sorted(snap_dir.glob("snapshot_*.csv")):
        d = date.fromisoformat(p.stem.removeprefix("snapshot_"))
        res = load_snapshot(con, d, read_snapshot(p))
        results.append({"date": d.isoformat(), **res})
    return results
