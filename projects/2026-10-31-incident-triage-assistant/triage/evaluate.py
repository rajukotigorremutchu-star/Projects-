"""Retrieval evaluation: does the right runbook come back for each labeled incident signature?"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .logparse import extract_signatures
from .retriever import BM25


def evaluate(cases: list[dict], index: BM25, normalize_queries: bool = True) -> dict:
    """Each case: {"id", "logs": [...], "expected_runbook": str|null}.
    Query is the top signature of the case. If expected is null, correct = no hit above threshold."""
    from .report import MIN_SCORE

    hits1 = hits3 = 0
    rr = 0.0
    rows = []
    pos = 0
    neg_ok = neg = 0
    for c in cases:
        sigs = extract_signatures(c["logs"])
        sig = sigs[0] if sigs else None
        if sig is None:
            query = ""
        else:
            query = f"{sig.service} {sig.text}" if normalize_queries else f"{sig.service} {sig.example}"
        ranked = [(ch.runbook_id, s) for ch, s in index.search_runbooks(query, k=3) if s >= MIN_SCORE]
        ids = [r for r, _ in ranked]
        exp = c["expected_runbook"]
        if exp is None:
            neg += 1
            neg_ok += int(not ids)
            rows.append({"id": c["id"], "expected": None, "got": ids[:1], "ok": not ids})
            continue
        pos += 1
        rank = ids.index(exp) + 1 if exp in ids else None
        hits1 += rank == 1
        hits3 += rank is not None
        rr += 1 / rank if rank else 0
        rows.append({"id": c["id"], "expected": exp, "got": ids[:3], "ok": rank == 1})
    return {
        "positives": pos, "hit@1": hits1 / pos if pos else 0.0, "hit@3": hits3 / pos if pos else 0.0,
        "mrr": rr / pos if pos else 0.0, "negatives": neg,
        "abstain_accuracy": neg_ok / neg if neg else None, "rows": rows,
    }


def random_baseline(cases: list[dict], runbook_ids: list[str], seed: int = 0) -> float:
    """Expected-style baseline: pick one random runbook per positive case (seeded)."""
    rng = random.Random(seed)
    pos = [c for c in cases if c["expected_runbook"]]
    return sum(rng.choice(runbook_ids) == c["expected_runbook"] for c in pos) / len(pos)


def load_cases(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text())
