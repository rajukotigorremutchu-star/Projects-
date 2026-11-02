"""CLI: python -m sessionizer.cli --db out.db [--input batches.jsonl]

Input is JSONL, one event per line, with an optional "_batch" field naming the batch.
Without --input, synthetic data is generated.
"""
from __future__ import annotations

import argparse
import json

from .generate import generate_batches
from .pipeline import Sessionizer


def load_jsonl(path: str) -> list[tuple[str, list[dict]]]:
    batches: dict[str, list[dict]] = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                ev = json.loads(line)
                batches.setdefault(str(ev.pop("_batch", "b0")), []).append(ev)
    return list(batches.items())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--input")
    ap.add_argument("--gap", type=int, default=1800)
    ap.add_argument("--lateness", type=int, default=600)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    sz = Sessionizer(a.db, a.gap, a.lateness)
    batches = load_jsonl(a.input) if a.input else [
        (f"b{i}", b) for i, b in enumerate(generate_batches(a.seed))]
    for bid, evs in batches:
        r = sz.process_batch(bid, evs)
        print(f"{bid}: accepted={r.accepted} dup={r.duplicates} late={r.late} "
              f"invalid={r.invalid} finalized={r.sessions_finalized} skipped={r.skipped}")
    print(f"flushed={sz.flush()}")
    for s in sz.sessions():
        print("session", *s)
    print("dead_letters:", len(sz.dead_letters()))


if __name__ == "__main__":
    main()
