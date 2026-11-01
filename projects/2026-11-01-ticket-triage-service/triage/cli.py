"""CLI: generate | etl | train | predict."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import etl, generate, model

ART = Path("artifacts")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="triage")
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--n", type=int, default=1500); g.add_argument("--seed", type=int, default=7)
    g.add_argument("--out", default=str(ART / "raw_tickets.csv"))
    e = sub.add_parser("etl"); e.add_argument("--raw", default=str(ART / "raw_tickets.csv")); e.add_argument("--db", default=str(ART / "tickets.db"))
    t = sub.add_parser("train"); t.add_argument("--db", default=str(ART / "tickets.db"))
    t.add_argument("--model", default=str(ART / "model.joblib")); t.add_argument("--report", default=str(ART / "report.json"))
    pr = sub.add_parser("predict"); pr.add_argument("text"); pr.add_argument("--model", default=str(ART / "model.joblib"))
    a = p.parse_args(argv)

    if a.cmd == "generate":
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        generate.generate_tickets(a.n, a.seed).to_csv(a.out, index=False)
        print(f"wrote {a.n} tickets to {a.out}")
    elif a.cmd == "etl":
        df, stats = etl.clean(pd.read_csv(a.raw))
        inserted = etl.load_sqlite(df, a.db)
        print(json.dumps({**stats, "inserted": inserted}, indent=2))
    elif a.cmd == "train":
        df = etl.read_sqlite(a.db)
        report = {"random_split": model.evaluate(df, "random"), "group_split": model.evaluate(df, "group")}
        model.train_final(df, a.model)
        model.save_report(report, a.report)
        print(json.dumps(report, indent=2))
    elif a.cmd == "predict":
        import joblib
        print(json.dumps(model.predict(joblib.load(a.model), [a.text])[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
