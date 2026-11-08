"""python -m logwatch.cli [--days N] [--seed S] [--sample-dir DIR]"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .generate import generate
from .pipeline import run


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--sample-dir", type=Path, help="also write a 200-row raw log sample here")
    a = ap.parse_args(argv)
    res = run(a.days, a.seed)
    print(json.dumps(res.metrics, indent=2))
    if a.sample_dir:
        logs, _, _ = generate(a.days, a.seed)
        a.sample_dir.mkdir(parents=True, exist_ok=True)
        logs.head(200).to_csv(a.sample_dir / "raw_logs_head.csv", index=False)
        (a.sample_dir / "expected_metrics.json").write_text(json.dumps(res.metrics, indent=2) + "\n")


if __name__ == "__main__":
    main()
