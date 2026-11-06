"""Replay a traffic trace (CSV: timestamp,key) against every algorithm.

    python simulate.py samples/trace.csv --limit 5 --window 10
"""
import argparse
import csv
import json

from ratelimiter.algorithms import ALGORITHMS, make_limiter


def load(path):
    with open(path, newline="") as f:
        return [(float(r["timestamp"]), r["key"]) for r in csv.DictReader(f)]


def simulate(trace, limit, window):
    out = {}
    for name in ALGORITHMS:
        now = [0.0]
        lim = make_limiter(name, limit, window, clock=lambda: now[0])
        allowed, accepted_ts = 0, []
        for ts, key in sorted(trace):
            now[0] = ts
            if lim.check(key).allowed:
                allowed += 1
                accepted_ts.append(ts)
        # worst-case accepted in any sliding window of length `window`
        worst, j = 0, 0
        for i, t in enumerate(accepted_ts):
            while accepted_ts[j] <= t - window:
                j += 1
            worst = max(worst, i - j + 1)
        out[name] = {"allowed": allowed, "rejected": len(trace) - allowed,
                     "max_in_any_window": worst}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("trace")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--window", type=float, default=10)
    a = ap.parse_args()
    print(json.dumps(simulate(load(a.trace), a.limit, a.window), indent=2))
