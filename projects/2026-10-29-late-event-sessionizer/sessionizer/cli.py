"""Usage: python -m sessionizer.cli BATCH_DIR [--db FILE] [--out sessions.csv]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import Sessionizer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Incremental late-event-aware sessionizer")
    ap.add_argument("batch_dir")
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--gap", type=int, default=30, help="session gap, minutes")
    ap.add_argument("--lateness", type=int, default=60, help="allowed lateness, minutes")
    ap.add_argument("--out", help="write sessions CSV here")
    a = ap.parse_args(argv)
    s = Sessionizer(a.db, a.gap, a.lateness)
    for p in sorted(Path(a.batch_dir).glob("*.csv")):
        print(json.dumps(s.process_file(p).as_dict()))
    print(json.dumps({"totals": s.counts()}))
    if a.out:
        s.export_sessions_csv(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
