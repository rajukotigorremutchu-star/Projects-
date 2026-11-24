from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import evaluate
from .pipeline import RunbookQA

ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="runbook-qa")
    p.add_argument("--runbooks", default=str(ROOT / "data/runbooks"))
    p.add_argument("--mode", choices=["extractive", "claude"], default="extractive")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--threshold", type=float, default=None)
    e = sub.add_parser("eval")
    e.add_argument("--questions", default=str(ROOT / "eval/questions.jsonl"))
    args = p.parse_args(argv)

    if args.cmd == "ask":
        qa = RunbookQA(args.runbooks, mode=args.mode)
        if args.threshold is not None:
            qa.threshold = args.threshold
        ans = qa.ask(args.question)
        print(json.dumps({"answer": ans.text, "citations": ans.citations, "abstained": ans.abstained}, indent=2))
    else:
        print(json.dumps(evaluate.run(args.runbooks, args.questions, args.mode), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
