"""Deterministic synthetic clickstream generator (with injected defects)."""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path

TYPES = ["page_view", "page_view", "page_view", "click", "add_to_cart", "purchase"]


def generate(path: Path, n_users: int = 20, day: str = "2026-11-01", seed: int = 7) -> int:
    rng = random.Random(seed)
    base = datetime.fromisoformat(day)
    lines = []
    eid = 0
    for u in range(n_users):
        t = base + timedelta(minutes=rng.randint(0, 600))
        for _ in range(rng.randint(1, 3)):  # 1-3 sessions per user
            for _ in range(rng.randint(2, 8)):
                eid += 1
                lines.append({"event_id": f"{day}-e{eid}", "user_id": f"u{u:03d}",
                              "event_type": rng.choice(TYPES),
                              "ts": t.isoformat() + "Z", "url": f"/p/{rng.randint(1, 30)}"})
                t += timedelta(seconds=rng.randint(5, 600))
            t += timedelta(minutes=rng.randint(45, 240))  # > 30 min gap
    out = [json.dumps(x) for x in lines]
    out.insert(3, out[2])                                  # duplicate
    out.insert(5, "{not json")                              # invalid json
    out.insert(7, json.dumps({"event_id": "x", "user_id": "u1", "event_type": "teleport", "ts": "2026-11-01T00:00:00Z"}))
    out.insert(9, json.dumps({"event_id": "y", "user_id": "u1", "event_type": "click", "ts": "yesterday"}))
    Path(path).write_text("\n".join(out) + "\n")
    return len(out)
