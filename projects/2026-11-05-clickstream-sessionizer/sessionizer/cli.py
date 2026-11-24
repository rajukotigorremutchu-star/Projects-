"""CLI: python -m sessionizer.cli run data/batches --db out/clicks.duckdb"""
from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import connect, export_parquet, ingest_batch


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="sessionizer")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ingest all *.jsonl batches in a directory")
    r.add_argument("batches")
    r.add_argument("--db", default=":memory:")
    r.add_argument("--gap-minutes", type=int, default=30)
    r.add_argument("--parquet-dir")
    a = ap.parse_args(argv)

    con = connect(a.db)
    for f in sorted(Path(a.batches).glob("*.jsonl")):
        res = ingest_batch(con, f, a.gap_minutes)
        print(res)
    print("\ndaily_metrics:")
    for row in con.execute("SELECT * FROM daily_metrics ORDER BY 1").fetchall():
        print(" ", row)
    if a.parquet_dir:
        print("parquet files:", len(export_parquet(con, Path(a.parquet_dir))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
