# Runbook Q&A Assistant (RAG with citations and abstention)

An AI-application project: ask natural-language questions over a set of ops runbooks and get a
**cited** answer, or an explicit "I could not find this" when the runbooks don't cover it.
Runs fully offline by default; an optional Claude mode is available.

## Problem
On-call engineers need a precise answer from the right runbook section, and a wrong confident answer
is worse than none. This project retrieves the best section, answers from it with a citation, and
abstains on out-of-scope questions. It ships with an evaluation set that measures all three.

## Architecture
```
data/runbooks/*.md ──chunker (one chunk per "##")──▶ BM25 index (pure Python, stemmed, title+heading indexed)
question ─▶ top-k chunks ─▶ coverage gate (abstain if best chunk covers < threshold of query terms)
                         └▶ answerer: extractive (offline, default) | claude (optional)
```
- `src/runbook_qa/chunker.py` – markdown → chunks with ids like `kafka-consumer-lag#alert-thresholds`
- `bm25.py` – Okapi BM25; deterministic tie-breaking
- `answerer.py` – extractive sentence selection; optional Anthropic Messages API call (stdlib `urllib`)
- `pipeline.py` – retrieval + abstention gate; `evaluate.py` – metrics; `cli.py` – CLI

## Setup
```bash
pip install -r requirements.txt     # only pytest, for the tests
export PYTHONPATH=src
```
No third-party runtime dependencies. Tested with Python 3.11 and 3.13.

## Usage
```bash
python -m runbook_qa.cli ask "How do I skip a poison message on a partition?"
python -m runbook_qa.cli ask "What is the vacation policy for engineers?"   # abstains
python -m runbook_qa.cli eval                                              # prints the metrics JSON
ANTHROPIC_API_KEY=... python -m runbook_qa.cli --mode claude ask "..."      # optional
python -m pytest -q
```
Expected outputs are in `samples/` (`expected_ask.json`, `expected_ask_abstain.json`, `expected_eval.json`);
the tests compare against them.

## Evaluation
`eval/questions.jsonl` has 36 hand-written questions (24 answerable with a gold chunk and answer keyword,
12 unanswerable) over 6 synthetic runbooks. Questions are split 50/50 into dev/test by SHA-256 of the id
(deterministic). The abstention threshold is **tuned on dev only**; results below are on the 18-question test split.

Measured (extractive mode, tuned threshold 0.5):

| Metric (test, n=18) | Never abstain (baseline) | With abstention |
|---|---|---|
| Overall correct (right answer+citation, or correct abstain) | 0.667 | 0.944 |
| Retrieval hit@3 / MRR (12 answerable) | 0.917 / 0.917 | same |
| Answer keyword rate | 0.917 | 0.917 |
| Citation correct rate | 0.917 | 0.917 |

The single miss on test is a retrieval failure. Claude mode was **not** evaluated here (no API access in the
build environment); `eval --mode claude` would run the same harness if you supply a key.

## Design decisions
- BM25 over embeddings: no model download, deterministic, easy to reason about and test.
- Abstention by query-term coverage of the top chunk: simple and cheap; threshold chosen on dev, reported on test.
- Citations are structural (chunk ids), and Claude-mode citations are filtered to ids actually retrieved.
- Extractive default means the tests and demo never require network or keys.

## Limitations
- Tiny, hand-written, synthetic eval set; the test split has only 18 questions, so numbers are coarse and
  one question moves them ~5 points. Questions were written by the same author as the runbooks (lexical bias).
- Known retrieval miss: "How do I promote the replica when the primary database dies?" ranks the
  "Rebuilding the old primary" section first (covered by a test). BM25 has no semantic understanding.
- Coverage-based abstention can be fooled by out-of-scope questions that reuse in-scope words.
- Extractive answers are verbatim sentences, not synthesized; multi-section answers aren't supported.
- Claude mode is untested against the live API.
