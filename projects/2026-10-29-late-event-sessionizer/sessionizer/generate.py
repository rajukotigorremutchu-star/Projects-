"""Deterministic synthetic clickstream batches with duplicates, late and bad rows."""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

TYPES = ["page_view", "click", "add_to_cart", "purchase"]
WEIGHTS = [6, 4, 2, 1]


def generate(out_dir: str | Path, n_users: int = 20, n_batches: int = 4, seed: int = 7) -> list[Path]:
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = datetime(2026, 10, 1, 8, 0, 0)
    events = []
    eid = 0
    for u in range(n_users):
        t = base + timedelta(minutes=rng.randint(0, 240))
        for _ in range(rng.randint(1, 3)):  # sessions per user
            for _ in range(rng.randint(2, 8)):
                eid += 1
                events.append((f"e{eid:05d}", f"u{u:03d}", t, rng.choices(TYPES, WEIGHTS)[0]))
                t += timedelta(minutes=rng.randint(0, 20))
            t += timedelta(minutes=rng.randint(40, 180))  # idle gap
    # arrival order: mostly by time, with jitter so some events land in later batches
    events.sort(key=lambda e: e[2] + timedelta(minutes=rng.randint(0, 150)))
    size = -(-len(events) // n_batches)
    paths = []
    for b in range(n_batches):
        chunk = events[b * size:(b + 1) * size]
        rows = [(e[0], e[1], e[2].isoformat(), e[3]) for e in chunk]
        if rows:
            rows.append(rows[rng.randrange(len(rows))])  # in-batch duplicate
        if b > 0:
            rows.append(_earlier_row(events, size, b, rng))  # cross-batch duplicate
        rows.append(("", "u999", base.isoformat(), "click"))                 # missing event id
        rows.append((f"bad{b}", f"u{b:03d}", "not-a-time", "click"))          # bad ts
        rows.append((f"bt{b}", f"u{b:03d}", base.isoformat(), "teleport"))    # bad type
        rng.shuffle(rows)
        p = out / f"batch_{b + 1:03d}.csv"
        with p.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["event_id", "user_id", "event_ts", "event_type"])
            w.writerows(rows)
        paths.append(p)
    return paths


def _earlier_row(events, size, b, rng):
    e = events[rng.randrange(size * b)]
    return (e[0], e[1], e[2].isoformat(), e[3])


if __name__ == "__main__":
    import sys
    generate(sys.argv[1] if len(sys.argv) > 1 else "batches")
