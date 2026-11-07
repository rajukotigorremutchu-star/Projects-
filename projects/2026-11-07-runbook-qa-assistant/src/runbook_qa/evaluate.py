"""Evaluation: retrieval (hit@k, MRR), abstention, answer keyword match, citation correctness.

Questions are split deterministically into dev/test by a hash of the id. The abstention
threshold is tuned on dev only; all headline numbers are reported on test.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .answerer import coverage
from .pipeline import RunbookQA

THRESHOLDS = [round(0.1 * i, 1) for i in range(0, 11)]


def load_questions(path: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text().splitlines() if l.strip()]


def split(questions: list[dict]) -> tuple[list[dict], list[dict]]:
    dev, test = [], []
    for q in questions:
        h = int(hashlib.sha256(q["id"].encode()).hexdigest(), 16) % 2
        (dev if h == 0 else test).append(q)
    return dev, test


def retrieval_metrics(qa: RunbookQA, qs: list[dict], k: int = 3) -> dict:
    ans = [q for q in qs if q.get("gold_chunk")]
    hit = rr = 0.0
    for q in ans:
        ids = [c.id for c, _ in qa.index.search(q["question"], k)]
        if q["gold_chunk"] in ids:
            hit += 1
            rr += 1 / (ids.index(q["gold_chunk"]) + 1)
    n = max(len(ans), 1)
    return {"n": len(ans), f"hit@{k}": hit / n, "mrr": rr / n}


def decision_accuracy(qa: RunbookQA, qs: list[dict], threshold: float) -> float:
    """Fraction of questions where answer-vs-abstain matches the label."""
    ok = 0
    for q in qs:
        hits = qa.retrieve(q["question"])
        abstain = not hits or coverage(q["question"], hits[0][0]) < threshold
        ok += abstain == (q.get("gold_chunk") is None)
    return ok / max(len(qs), 1)


def tune_threshold(qa: RunbookQA, dev: list[dict]) -> float:
    # highest accuracy; ties broken toward the lower threshold (answer more)
    return max(THRESHOLDS, key=lambda t: (decision_accuracy(qa, dev, t), -t))


def end_to_end(qa: RunbookQA, qs: list[dict]) -> dict:
    tot = len(qs)
    correct = kw_ok = cite_ok = answered_gold = 0
    for q in qs:
        a = qa.ask(q["question"])
        gold = q.get("gold_chunk")
        if gold is None:
            correct += a.abstained
            continue
        if a.abstained:
            continue
        answered_gold += 1
        text = a.text.lower()
        kw = all(k.lower() in text for k in q["answer_keywords"])
        cite = a.citations == [gold]
        kw_ok += kw
        cite_ok += cite
        correct += kw and cite
    ng = sum(1 for q in qs if q.get("gold_chunk"))
    return {
        "n": tot,
        "answerable": ng,
        "unanswerable": tot - ng,
        "answered_of_answerable": answered_gold,
        "answer_keyword_rate": kw_ok / max(ng, 1),
        "citation_correct_rate": cite_ok / max(ng, 1),
        "overall_correct": correct / max(tot, 1),
    }


def run(runbooks: str | Path, questions: str | Path, mode: str = "extractive") -> dict:
    qs = load_questions(questions)
    dev, test = split(qs)
    qa = RunbookQA(runbooks, mode=mode)
    thr = tune_threshold(qa, dev)
    qa.threshold = thr
    base = RunbookQA(runbooks, mode=mode, threshold=0.0)  # baseline: never abstain
    return {
        "mode": mode,
        "dev_size": len(dev),
        "test_size": len(test),
        "tuned_threshold": thr,
        "retrieval_test": retrieval_metrics(qa, test),
        "baseline_never_abstain_test": end_to_end(base, test),
        "with_abstention_test": end_to_end(qa, test),
    }
