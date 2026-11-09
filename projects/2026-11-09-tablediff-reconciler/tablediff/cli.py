from __future__ import annotations

import argparse
import json
import sys

import duckdb

from .core import DiffError, diff_tables, load_csv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="tablediff", description="Reconcile two CSV tables by integer key.")
    p.add_argument("source")
    p.add_argument("target")
    p.add_argument("--key", required=True)
    p.add_argument("--ignore", nargs="*", default=[], help="columns to exclude")
    p.add_argument("--threshold", type=int, default=64, help="max rows in a mismatching segment before it is split")
    p.add_argument("--precision", type=int, default=6, help="decimals for numeric comparison")
    p.add_argument("--no-numeric-normalize", action="store_true", help="compare numbers as text")
    p.add_argument("--no-trim", action="store_true", help="do not trim whitespace")
    p.add_argument("--out", help="write JSON report here (default: stdout)")
    a = p.parse_args(argv)
    con = duckdb.connect()
    try:
        load_csv(con, "source", a.source)
        load_csv(con, "target", a.target)
        res = diff_tables(con, "source", "target", a.key, a.ignore, a.threshold, a.precision,
                          not a.no_numeric_normalize, not a.no_trim)
    except (DiffError, duckdb.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    text = json.dumps(res.to_dict(), indent=2) + "\n"
    if a.out:
        with open(a.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text)
    return 0 if res.identical else 1
