"""Deterministic synthetic clickstream generator (out-of-order, late, duplicate, invalid)."""
from __future__ import annotations

import random


def generate_batches(seed: int = 7, users: int = 5, batch_size: int = 20) -> list[list[dict]]:
    rng = random.Random(seed)
    events, n = [], 0
    for u in range(users):
        t = 1_700_000_000 + rng.randint(0, 600)
        for _ in range(rng.randint(2, 4)):  # sessions per user
            for _ in range(rng.randint(3, 8)):
                t += rng.randint(10, 600)
                n += 1
                events.append({"event_id": f"e{n}", "user_id": f"u{u}", "event_time": t})
            t += rng.randint(3600, 7200)  # idle gap -> new session
    # arrival order: event time order with local jitter (out-of-order)
    events.sort(key=lambda e: e["event_time"] + rng.randint(-400, 400))
    out = list(events)
    # inject duplicates, a very late straggler, and invalid rows
    for _ in range(4):
        out.insert(rng.randint(5, len(out)), dict(rng.choice(events)))
    straggler = dict(events[0], event_id="straggler")
    straggler["event_time"] -= 5000
    out.append(straggler)
    out.insert(3, {"event_id": "bad1", "user_id": "u0", "event_time": "yesterday"})
    out.insert(9, {"user_id": "u1", "event_time": 1_700_000_100})
    return [out[i:i + batch_size] for i in range(0, len(out), batch_size)]
