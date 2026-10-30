"""CLI: replay a request trace through one or more limiter configs.

    python -m ratelimitkit simulate samples/scenario.json
"""

from __future__ import annotations

import argparse
import json
import sys

from .algorithms import build_algorithm
from .limiter import Limiter


def simulate(scenario: dict) -> dict:
    """Replay ``scenario["requests"]`` (t, key, optional cost) against each limiter."""
    results = {}
    for name, spec in scenario["limiters"].items():
        clock = {"t": 0.0}
        limiter = Limiter(build_algorithm(spec), clock=lambda c=clock: c["t"])
        decisions = []
        for req in sorted(scenario["requests"], key=lambda r: r["t"]):
            clock["t"] = req["t"]
            d = limiter.hit(req["key"], req.get("cost", 1))
            decisions.append({
                "t": req["t"], "key": req["key"], "allowed": d.allowed,
                "remaining": d.remaining,
                "retry_after": None if d.retry_after is None else round(d.retry_after, 3),
            })
        results[name] = {
            "allowed": sum(d["allowed"] for d in decisions),
            "denied": sum(not d["allowed"] for d in decisions),
            "decisions": decisions,
        }
    return results


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ratelimitkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("simulate", help="replay a scenario JSON file")
    s.add_argument("scenario")
    s.add_argument("--summary", action="store_true", help="only print allowed/denied counts")
    args = p.parse_args(argv)

    with open(args.scenario) as f:
        out = simulate(json.load(f))
    if args.summary:
        out = {k: {"allowed": v["allowed"], "denied": v["denied"]} for k, v in out.items()}
    json.dump(out, sys.stdout, indent=2)
    print()
    return 0
