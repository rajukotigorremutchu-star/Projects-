"""CLI: python -m scd2.cli {generate,load} ..."""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from . import pipeline
from .generate import generate


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="scd2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic snapshots")
    g.add_argument("out_dir", type=Path)
    g.add_argument("--days", type=int, default=10)
    g.add_argument("--customers", type=int, default=50)
    g.add_argument("--seed", type=int, default=7)
    l = sub.add_parser("load", help="build the SCD2 dimension from snapshots")
    l.add_argument("snap_dir", type=Path)
    l.add_argument("--db", default=":memory:")
    l.add_argument("--export", type=Path, help="write dimension CSV")
    l.add_argument("--as-of", type=date.fromisoformat)
    a = ap.parse_args(argv)

    if a.cmd == "generate":
        paths = generate(a.out_dir, a.days, a.customers, a.seed)
        print(f"wrote {len(paths)} snapshots to {a.out_dir}")
        return 0
    con = pipeline.connect(a.db)
    for r in pipeline.run_directory(con, a.snap_dir):
        print(r)
    checks = pipeline.quality_checks(con)
    print("quality:", checks)
    n = con.execute("SELECT count(*), count(*) FILTER (WHERE is_current) FROM dim_customer").fetchone()
    print(f"dim_customer rows={n[0]} current={n[1]}")
    if a.as_of:
        print(f"as of {a.as_of}: {len(pipeline.as_of(con, a.as_of))} active customers")
    if a.export:
        a.export.parent.mkdir(parents=True, exist_ok=True)
        pipeline.export_dimension(con, a.export)
    return 1 if any(checks.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
