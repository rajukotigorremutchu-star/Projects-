# Incident Triage Assistant (RAG over runbooks, local-first)

Given a raw application log, find the most important WARN+ error patterns, retrieve the matching on-call runbook, and produce a grounded triage report (severity + the runbook's own mitigation steps). Runs fully offline; an optional Claude backend can phrase the summary.

## Architecture

```
log file -> logparse (parse, mask ids/IPs/paths/numbers, group into signatures)
         -> retriever (BM25 over runbook sections, best section per runbook)
         -> report   (mock: extractive, deterministic | anthropic: LLM summary of retrieved steps only)
data/runbooks/*.md   8 synthetic runbooks      data/eval_cases.json  26 labeled incidents
```

- `triage/logparse.py` - line parser and normalizer; signatures sorted by severity then count.
- `triage/retriever.py` - dependency-free BM25; one chunk per `##` section.
- `triage/report.py` - severity rules (CRITICAL->SEV1, ERROR with >=10 occurrences->SEV2, else SEV3), abstains ("escalate") when the best score < `MIN_SCORE`.
- `triage/evaluate.py` - hit@1, hit@3, MRR, abstention accuracy, seeded random baseline.

## Setup & usage

```bash
pip install -r requirements.txt          # pytest only; core uses the stdlib
python -m triage report samples/incident_db.log     # mock backend (default)
python -m triage eval                    # retrieval evaluation
python -m pytest -q
# optional LLM backend:
pip install -r requirements-llm.txt && ANTHROPIC_API_KEY=... python -m triage report samples/incident_db.log --backend anthropic
```

Sample input: `samples/incident_db.log`; expected outputs: `samples/expected_report.txt`, `samples/expected_eval.txt` (a test asserts the report matches byte-for-byte).

## Measured evaluation (run on this repo's data)

24 positive cases + 2 negatives (no runbook should match):

| Method | hit@1 | hit@3 | MRR |
|---|---|---|---|
| BM25 on normalized signature | 0.958 | 1.000 | 0.972 |
| BM25 on raw example line | 0.958 | 1.000 | 0.972 |
| Random runbook (seed 0) | 0.125 | - | - |

Abstention: 1 of 2 negatives correctly produced no match (0.5). Misses: `oom-3` (a Java `OutOfMemoryError` heap-space line ranks `oom-killed` third; the runbook never uses that exact wording) and `neg-2` (a generic word overlap with the rate-limit runbook).

## Design decisions

- **Extractive by default**: the mock report only repeats runbook steps, so it cannot hallucinate remediation commands; the LLM prompt is likewise restricted to the retrieved steps.
- **BM25 over embeddings**: no model downloads, deterministic, fast, easy to debug.
- **Abstention threshold** (`MIN_SCORE=1.0`) is deliberately not tuned on the eval set to avoid optimistic numbers; the sample report shows a weak 3.02-score match for "retrying query", which a stricter threshold would drop.

## Limitations

- Eval data and runbooks are synthetic and written by the same author, so vocabulary overlap is higher than in real incidents; the numbers are not a claim about production accuracy. The raw-vs-normalized comparison shows no difference on this small set.
- Only the top signature per case is evaluated; the severity rules are simple heuristics.
- The Anthropic backend is not exercised by tests or the measured results (no API access in CI); it is a thin wrapper.
- Log format is a single timestamp/level/[service]/message pattern; multi-line stack traces are not joined.
