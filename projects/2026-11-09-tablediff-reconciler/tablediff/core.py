"""Checksum-bisection table reconciliation on DuckDB.

Both tables are reduced to (key, row_hash). Key ranges are compared via an
order-independent aggregate (count, sum of hashes). Matching ranges are pruned
without reading row values; only mismatching ranges small enough are diffed
row-by-row with a FULL OUTER JOIN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import duckdb

NULL_MARK = "\u0000NULL"
SEP = "\u001f"


class DiffError(ValueError):
    """Invalid input (missing key column, duplicate or non-integer keys, ...)."""


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def load_csv(con: duckdb.DuckDBPyConnection, table: str, path: str) -> None:
    """Load a CSV with every column as VARCHAR (types are normalised later)."""
    con.execute(
        f"CREATE OR REPLACE TABLE {_q(table)} AS "
        "SELECT * FROM read_csv(?, header=true, all_varchar=true)",
        [str(path)],
    )


def _columns(con, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE {_q(table)}").fetchall()]


def _norm(col: str, precision: int, numeric: bool, trim: bool) -> str:
    """SQL expression giving a canonical VARCHAR (NULL stays NULL) for a column."""
    c = _q(col)
    txt = f"trim({c})" if trim else c
    if not numeric:
        return f"CAST({txt} AS VARCHAR)"
    return (
        f"CASE WHEN try_cast({txt} AS DOUBLE) IS NOT NULL "
        f"AND isfinite(try_cast({txt} AS DOUBLE)) "
        f"THEN CAST(round(try_cast({txt} AS DOUBLE), {precision}) AS VARCHAR) "
        f"ELSE CAST({txt} AS VARCHAR) END"
    )


@dataclass
class DiffResult:
    key: str
    source_rows: int
    target_rows: int
    missing_in_target: list[int] = field(default_factory=list)
    extra_in_target: list[int] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    columns_only_in_source: list[str] = field(default_factory=list)
    columns_only_in_target: list[str] = field(default_factory=list)
    compared_columns: list[str] = field(default_factory=list)
    segments_checked: int = 0
    segments_pruned: int = 0
    rows_detail_compared: int = 0

    @property
    def identical(self) -> bool:
        return not (self.missing_in_target or self.extra_in_target or self.changed)

    def to_dict(self) -> dict[str, Any]:
        matched = self.source_rows - len(self.missing_in_target)
        return {
            "key": self.key,
            "compared_columns": self.compared_columns,
            "columns_only_in_source": self.columns_only_in_source,
            "columns_only_in_target": self.columns_only_in_target,
            "summary": {
                "source_rows": self.source_rows,
                "target_rows": self.target_rows,
                "missing_in_target": len(self.missing_in_target),
                "extra_in_target": len(self.extra_in_target),
                "changed_rows": len(self.changed),
                "identical_rows": matched - len(self.changed),
            },
            "missing_in_target": self.missing_in_target,
            "extra_in_target": self.extra_in_target,
            "changed": self.changed,
            "stats": {
                "segments_checked": self.segments_checked,
                "segments_pruned_by_checksum": self.segments_pruned,
                "rows_detail_compared": self.rows_detail_compared,
            },
        }


def _prepare(con, table, key, cols, precision, numeric, trim, ignore_case_unused=None):
    """Create hash table `_h_<table>` and validate the key."""
    if key not in _columns(con, table):
        raise DiffError(f"key column {key!r} not found in {table!r}")
    bad = con.execute(
        f"SELECT count(*) FROM {_q(table)} WHERE try_cast({_q(key)} AS BIGINT) IS NULL"
    ).fetchone()[0]
    if bad:
        raise DiffError(f"{table}: {bad} key value(s) are NULL or not integers")
    dup = con.execute(
        f"SELECT count(*) FROM (SELECT {_q(key)} FROM {_q(table)} "
        f"GROUP BY 1 HAVING count(*) > 1)"
    ).fetchone()[0]
    if dup:
        raise DiffError(f"{table}: {dup} duplicated key value(s); key must be unique")
    parts = [
        f"coalesce({_norm(c, precision, numeric, trim)}, '{NULL_MARK}')" for c in cols
    ]
    expr = "concat_ws('" + SEP + "', " + ", ".join(parts) + ")" if parts else "''"
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE _h_{table} AS "
        f"SELECT CAST({_q(key)} AS BIGINT) AS k, "
        f"CAST(hash({expr}) AS HUGEINT) AS h FROM {_q(table)}"
    )


def diff_tables(
    con: duckdb.DuckDBPyConnection,
    source: str,
    target: str,
    key: str,
    ignore: list[str] | None = None,
    threshold: int = 64,
    precision: int = 6,
    numeric_normalize: bool = True,
    trim: bool = True,
) -> DiffResult:
    """Diff two tables already present in `con`. Key must be a unique integer column."""
    ignore = set(ignore or [])
    scols, tcols = _columns(con, source), _columns(con, target)
    common = [c for c in scols if c in tcols and c != key and c not in ignore]
    res = DiffResult(
        key=key,
        source_rows=0,
        target_rows=0,
        columns_only_in_source=[c for c in scols if c not in tcols and c not in ignore],
        columns_only_in_target=[c for c in tcols if c not in scols and c not in ignore],
        compared_columns=common,
    )
    for t in (source, target):
        _prepare(con, t, key, common, precision, numeric_normalize, trim)
    res.source_rows = con.execute(f"SELECT count(*) FROM _h_{source}").fetchone()[0]
    res.target_rows = con.execute(f"SELECT count(*) FROM _h_{target}").fetchone()[0]
    bounds = con.execute(
        f"SELECT min(k), max(k) FROM (SELECT k FROM _h_{source} UNION ALL SELECT k FROM _h_{target})"
    ).fetchone()
    if bounds[0] is None:
        return res

    def checksum(t: str, lo: int, hi: int):
        return con.execute(
            f"SELECT count(*), coalesce(sum(h + k), 0) FROM _h_{t} WHERE k BETWEEN ? AND ?",
            [lo, hi],
        ).fetchone()

    norm = {c: _norm(c, precision, numeric_normalize, trim) for c in common}
    kq = _q(key)

    def detail(lo: int, hi: int) -> None:
        sel = ", ".join(
            f"{norm[c].replace(_q(c), 's.' + _q(c))} AS s_{i}, "
            f"{norm[c].replace(_q(c), 't.' + _q(c))} AS t_{i}"
            for i, c in enumerate(common)
        )
        rows = con.execute(
            f"SELECT CAST(coalesce(s.{kq}, t.{kq}) AS BIGINT) AS k, "
            f"s.{kq} IS NOT NULL AS in_s, t.{kq} IS NOT NULL AS in_t"
            + (", " + sel if sel else "")
            + f" FROM (SELECT * FROM {_q(source)} WHERE CAST({kq} AS BIGINT) BETWEEN ? AND ?) s "
            f"FULL OUTER JOIN (SELECT * FROM {_q(target)} WHERE CAST({kq} AS BIGINT) BETWEEN ? AND ?) t "
            f"ON CAST(s.{kq} AS BIGINT) = CAST(t.{kq} AS BIGINT) ORDER BY 1",
            [lo, hi, lo, hi],
        ).fetchall()
        res.rows_detail_compared += len(rows)
        for r in rows:
            k, in_s, in_t, vals = r[0], r[1], r[2], r[3:]
            if in_s and not in_t:
                res.missing_in_target.append(k)
            elif in_t and not in_s:
                res.extra_in_target.append(k)
            else:
                diffs = {
                    c: {"source": vals[2 * i], "target": vals[2 * i + 1]}
                    for i, c in enumerate(common)
                    if vals[2 * i] != vals[2 * i + 1]
                }
                if diffs:
                    res.changed.append({"key": k, "columns": diffs})

    def walk(lo: int, hi: int) -> None:
        res.segments_checked += 1
        cs, ct = checksum(source, lo, hi), checksum(target, lo, hi)
        if cs == ct:
            res.segments_pruned += 1
            return
        if lo == hi or cs[0] + ct[0] <= threshold:
            detail(lo, hi)
            return
        mid = (lo + hi) // 2
        walk(lo, mid)
        walk(mid + 1, hi)

    walk(bounds[0], bounds[1])
    return res
