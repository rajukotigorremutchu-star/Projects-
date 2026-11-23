# Ticket Triage Service (data + software + ML)

End-to-end support-ticket routing: synthetic tickets → ETL into SQLite → TF-IDF/logistic-regression classifier (with a majority baseline) → FastAPI service that serves and logs predictions. Runs fully locally, no paid services or external data.

## Problem
Support teams route tickets by hand. This project classifies a ticket into `billing`, `bug`, `account_access`, `feature_request`, or `shipping`, and flags low-confidence predictions (`needs_review`) for a human.

## Architecture
```
generate.py  -> raw_tickets.csv
etl.py       -> normalize, drop empty/bad-label rows, dedupe by text hash -> SQLite `tickets`
model.py     -> split (random stratified | held-out templates) -> baseline + TF-IDF/LogReg -> report.json, model.joblib
api.py       -> FastAPI: /health /predict /predict/batch /stats; logs to SQLite `predictions`
cli.py       -> generate | etl | train | predict
```

## Setup
```bash
cd projects/2026-11-01-ticket-triage-service
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Usage
```bash
python -m triage.cli generate          # artifacts/raw_tickets.csv (1500 rows, seed 7)
python -m triage.cli etl               # artifacts/tickets.db
python -m triage.cli train             # prints metrics, saves artifacts/model.joblib + report.json
python -m triage.cli predict "I was charged twice this month"
uvicorn triage.api:app_factory --factory --port 8000
curl -X POST localhost:8000/predict -H 'content-type: application/json' \
     -d '{"text":"I cannot log in, my account is locked"}'
pytest                                  # 17 tests
```
Env vars: `TRIAGE_MODEL`, `TRIAGE_DB`.

Samples in `samples/`: `sample_tickets.csv` (input format), `expected_report.json` (output of the commands above), `expected_predict.json` (API response for the curl above).

## Measured results (default seed, 1092 tickets after dedup)
| Split | Model | Accuracy | Macro-F1 |
|---|---|---|---|
| random stratified (819 train / 273 test) | majority baseline | 0.2491 | 0.0798 |
| random stratified | TF-IDF + LogReg | 0.9780 | 0.9766 |
| held-out templates (781 / 311) | majority baseline | 0.0932 | 0.0341 |
| held-out templates | TF-IDF + LogReg | 0.1929 | 0.1983 |

Primary metric: macro-F1 (classes are balanced-ish but macro-F1 penalizes ignoring any class).

## Design decisions
- **Leakage control:** exact-duplicate texts are removed in ETL *before* splitting; the TF-IDF vectorizer sits inside the sklearn pipeline so it is fit on training data only; tests assert train/test are disjoint by id and text hash; splits use a fixed seed.
- **Two splits:** the random split measures in-distribution performance; the held-out-template split measures generalization to unseen phrasing.
- **Idempotent ETL:** `INSERT OR IGNORE` on ticket id and text hash.
- **Low-confidence flag** (threshold 0.5) instead of forcing a label.

## Limitations
- Data is synthetic and template-generated, so the 0.98 random-split score is optimistic and says little about real tickets.
- The held-out-template result (~0.20 macro-F1, barely above chance for 5 classes) shows the model largely memorizes template phrasing; each template here shares little vocabulary with others, which is an artifact of the generator, but it is a real warning that this model was not shown to generalize. Real data or paraphrase-rich data (or embeddings) would be needed.
- Evaluation is a single split, no confidence intervals or probability calibration; the 0.5 review threshold is untuned.
- The service has no auth or rate limiting and uses a new SQLite connection per request.
