from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from .benchmark import format_report, run_benchmark
from .data import generate_demand


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="demandcast")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write synthetic demand CSV")
    g.add_argument("--out", required=True)
    g.add_argument("--series", type=int, default=12)
    g.add_argument("--days", type=int, default=540)
    g.add_argument("--seed", type=int, default=42)
    e = sub.add_parser("evaluate", help="run benchmark on a CSV")
    e.add_argument("--data", required=True)
    e.add_argument("--seed", type=int, default=0)
    e.add_argument("--json", help="also write full results JSON here")
    a = p.parse_args(argv)
    if a.cmd == "generate":
        generate_demand(a.series, a.days, seed=a.seed).to_csv(a.out, index=False)
        return 0
    df = pd.read_csv(a.data, parse_dates=["date"])
    res = run_benchmark(df, seed=a.seed)
    print(format_report(res))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
