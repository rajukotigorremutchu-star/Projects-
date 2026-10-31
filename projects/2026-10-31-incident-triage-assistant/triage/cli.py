from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .evaluate import evaluate, load_cases, random_baseline
from .report import build_findings, llm_report, mock_report
from .retriever import BM25, load_runbooks

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="triage")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="triage a log file")
    r.add_argument("logfile", type=Path)
    r.add_argument("--runbooks", type=Path, default=ROOT / "data" / "runbooks")
    r.add_argument("--backend", choices=["mock", "anthropic"], default="mock")
    e = sub.add_parser("eval", help="evaluate retrieval on labeled cases")
    e.add_argument("--cases", type=Path, default=ROOT / "data" / "eval_cases.json")
    e.add_argument("--runbooks", type=Path, default=ROOT / "data" / "runbooks")
    a = ap.parse_args(argv)

    index = BM25(load_runbooks(a.runbooks))
    if a.cmd == "report":
        findings = build_findings(a.logfile.read_text().splitlines(), index)
        print(llm_report(findings) if a.backend == "anthropic" else mock_report(findings))
        return 0
    cases = load_cases(a.cases)
    ids = sorted({c.runbook_id for c in index.chunks})
    for label, norm in (("normalized", True), ("raw-example", False)):
        res = evaluate(cases, index, normalize_queries=norm)
        print(f"[{label}] positives={res['positives']} hit@1={res['hit@1']:.3f} hit@3={res['hit@3']:.3f} "
              f"mrr={res['mrr']:.3f} abstain_acc={res['abstain_accuracy']}")
        if norm:
            for row in res["rows"]:
                if not row["ok"]:
                    print("  miss:", row)
    print(f"[random baseline, seed 0] hit@1={random_baseline(cases, ids):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
