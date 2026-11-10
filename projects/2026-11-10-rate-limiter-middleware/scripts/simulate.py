"""Deterministic comparison of algorithms on a boundary-burst workload.

Workload: limit=10 per 10s. A client sends 10 requests at t=9.9s and 10 more at
t=10.1s (straddling a fixed-window boundary), then 1 request/s steady traffic.
Reports accepted requests within any 10s sliding span (ground truth: <= 10).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ratelimit import ManualClock, make_limiter  # noqa: E402

LIMIT, WINDOW = 10, 10.0


def workload():
    ev = [9.9] * 10 + [10.1] * 10 + [20.0 + i for i in range(1, 11)]
    return sorted(ev)


def run(name):
    clock = ManualClock()
    lim = make_limiter(name, LIMIT, WINDOW, clock)
    accepted = []
    for t in workload():
        clock.t = t
        if lim.check("c").allowed:
            accepted.append(t)
    worst = max(sum(1 for u in accepted if t <= u < t + WINDOW) for t in accepted)
    return {"algorithm": name, "accepted": len(accepted),
            "max_in_any_10s_span": worst, "violates_limit": worst > LIMIT}


if __name__ == "__main__":
    out = [run(n) for n in ["fixed_window", "sliding_log", "sliding_counter", "token_bucket"]]
    print(json.dumps(out, indent=2))
