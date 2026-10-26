"""Deterministic synthetic daily customer snapshots."""
from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

CITIES = ["Austin", "Berlin", "Chennai", "Dublin", "Osaka"]
PLANS = ["free", "pro", "team"]


def generate(out_dir: Path, days: int = 10, customers: int = 50, seed: int = 7,
             start: date = date(2026, 1, 1)) -> list[Path]:
    rng = random.Random(seed)
    state = {i: {"name": f"Customer {i}", "city": rng.choice(CITIES), "plan": "free"}
             for i in range(1, customers + 1)}
    next_id = customers + 1
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for n in range(days):
        if n:
            for cid in list(state):
                r = rng.random()
                if r < 0.08:
                    state[cid]["plan"] = rng.choice(PLANS)
                elif r < 0.12:
                    state[cid]["city"] = rng.choice(CITIES)
                elif r < 0.14:
                    del state[cid]  # churned / removed from source
            for _ in range(rng.randint(0, 3)):
                state[next_id] = {"name": f"Customer {next_id}", "city": rng.choice(CITIES), "plan": "free"}
                next_id += 1
        p = out_dir / f"snapshot_{start + timedelta(days=n)}.csv"
        with open(p, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["customer_id", "name", "city", "plan"])
            for cid in sorted(state):
                w.writerow([cid, *state[cid].values()])
        paths.append(p)
    return paths
