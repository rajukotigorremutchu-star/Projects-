import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from runbook_qa import evaluate  # noqa: E402
from runbook_qa.answerer import ABSTAIN_TEXT  # noqa: E402
from runbook_qa.bm25 import BM25  # noqa: E402
from runbook_qa.chunker import chunk_markdown, load_chunks  # noqa: E402
from runbook_qa.pipeline import RunbookQA  # noqa: E402
from runbook_qa.text import tokenize  # noqa: E402

RB = ROOT / "data/runbooks"
QS = ROOT / "eval/questions.jsonl"


def test_chunker_splits_sections_and_ids():
    md = "# Title\n\n## First Part\nhello world.\n\n## Second Part\nbye.\n"
    cs = chunk_markdown("doc", md)
    assert [c.id for c in cs] == ["doc#first-part", "doc#second-part"]
    assert cs[0].doc == "Title" and cs[0].text == "hello world."


def test_load_chunks_unique_ids():
    ids = [c.id for c in load_chunks(RB)]
    assert len(ids) == len(set(ids)) >= 18


def test_tokenize_drops_stopwords_and_stems():
    assert "the" not in tokenize("the failovers")
    assert tokenize("failovers") == tokenize("failover")


def test_bm25_ranks_relevant_chunk_first_and_is_deterministic():
    idx = BM25(load_chunks(RB))
    r1 = idx.search("kafka poison message skip offset")
    assert r1[0][0].id == "kafka-consumer-lag#skipping-a-poison-message"
    assert r1 == idx.search("kafka poison message skip offset")
    assert idx.search("zzzz qqqq") == []


def test_answer_cites_gold_chunk():
    qa = RunbookQA(RB, threshold=0.5)
    a = qa.ask("What does pg_is_in_recovery() need to return after promotion?")
    assert not a.abstained
    assert a.citations == ["postgres-failover#promoting-the-replica"]
    assert "false" in a.text


def test_known_retrieval_miss_is_documented():
    # Known limitation (see README): lexical overlap sends this to the wrong section.
    qa = RunbookQA(RB)
    top = qa.retrieve("How do I promote the replica when the primary database dies?")[0][0].id
    assert top == "postgres-failover#rebuilding-the-old-primary"


def test_abstains_on_out_of_scope():
    qa = RunbookQA(RB, threshold=0.5)
    a = qa.ask("What is the vacation policy for engineers?")
    assert a.abstained and a.text == ABSTAIN_TEXT and a.citations == []


def test_claude_mode_requires_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    qa = RunbookQA(RB, mode="claude", threshold=0.0)
    with pytest.raises(RuntimeError):
        qa.ask("How do I promote the replica?")


def test_split_is_deterministic_disjoint_and_nonempty():
    qs = evaluate.load_questions(QS)
    d1, t1 = evaluate.split(qs)
    d2, t2 = evaluate.split(qs)
    assert d1 == d2 and t1 == t2
    assert not {q["id"] for q in d1} & {q["id"] for q in t1}
    assert len(d1) + len(t1) == len(qs)
    assert any(q["gold_chunk"] is None for q in d1) and any(q["gold_chunk"] is None for q in t1)


def test_gold_chunks_exist():
    ids = {c.id for c in load_chunks(RB)}
    for q in evaluate.load_questions(QS):
        assert q["gold_chunk"] is None or q["gold_chunk"] in ids


def test_threshold_tuned_on_dev_only():
    qs = evaluate.load_questions(QS)
    dev, _ = evaluate.split(qs)
    qa = RunbookQA(RB)
    t = evaluate.tune_threshold(qa, dev)
    best = evaluate.decision_accuracy(qa, dev, t)
    assert all(evaluate.decision_accuracy(qa, dev, x) <= best for x in evaluate.THRESHOLDS)


def test_eval_report_matches_expected_sample():
    rep = evaluate.run(RB, QS)
    exp = json.loads((ROOT / "samples/expected_eval.json").read_text())
    assert rep == exp
    # abstention must beat never-abstain on overall correctness
    assert rep["with_abstention_test"]["overall_correct"] > rep["baseline_never_abstain_test"]["overall_correct"]


def test_cli_ask_matches_sample():
    out = subprocess.run(
        [sys.executable, "-m", "runbook_qa.cli", "ask", "How do I skip a poison message on a partition?"],
        capture_output=True, text=True, cwd=ROOT, env={"PYTHONPATH": str(ROOT / "src"), "PATH": ""},
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == json.loads((ROOT / "samples/expected_ask.json").read_text())
