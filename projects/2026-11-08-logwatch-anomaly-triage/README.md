# Logwatch: Anomaly Triage for Service Logs

Data engineering + software engineering + ML in one small, runnable project.

**Problem.** On-call engineers drown in per-minute dashboards. This project turns raw HTTP request logs into per-service per-minute features (DuckDB), detects incident minutes with two unsupervised detectors, evaluates them against known injected incidents, and serves a FastAPI `/score` endpoint that returns a verdict plus human-readable reasons.

## Architecture

```
generate.py  synthetic request logs (3 services, diurnal traffic) + labelled incidents
   -> etl.py        DuckDB SQL: gap-free per-service/minute requests, error_rate, p95_ms
   -> evaluate.py   chronological split (first 4 days train, last 2 days test)
   -> detect.py     RobustZBaseline (seasonal median/MAD)  vs  IsolationForest
   -> triage.py     reasons ("error rate 0.41 is above typical 0 ... z=+18.9")
   -> api.py        FastAPI: /health, /metrics, /score
```

Incident types injected (8-20 min each, 2 per service per day): `error_spike` (35% 5xx), `latency_spike` (5x latency), `traffic_drop` (10% of normal volume).

## Setup & usage

```bash
cd projects/2026-11-08-logwatch-anomaly-triage
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m logwatch.cli                      # prints metrics JSON
python -m logwatch.cli --sample-dir samples # regenerates samples/
uvicorn logwatch.api:app                    # trains at startup (~2 s), then serves
curl -s localhost:8000/score -H 'content-type: application/json' -d @samples/score_request_anomaly.json
```

No network, API keys, or paid services are needed; all data is synthetic and seeded.

## Samples
- `samples/raw_logs_head.csv`: first 200 raw log rows (input shape).
- `samples/expected_metrics.json`: exact CLI output for seed 7 / 6 days (a test asserts equality).
- `samples/score_request_{normal,anomaly}.json` -> `samples/score_response_{normal,anomaly}.json`.

## Measured results (seed 7, 6 days, test = last 2 days, 254 incident minutes, 18 incidents)

| Detector | Precision | Recall | F1 | Incidents detected |
|---|---|---|---|---|
| Robust seasonal z-score (baseline) | 0.854 | 0.945 | 0.897 | 18 / 18 |
| IsolationForest (contamination 0.01) | 0.838 | 0.224 | 0.354 | 10 / 18 |

These come from a single seed on synthetic data; they show how the pipeline behaves, not real-world accuracy. Run the CLI to reproduce them.

## Design decisions
- **Leakage control:** split is chronological and day-aligned; both detectors fit on train only; IsolationForest's threshold is a quantile of *training* scores; labels are never used for fitting. The z threshold (6.0) was fixed a priori.
- **Noise floors:** my first run produced precision 0.07 for the baseline because the median error rate is 0, giving MAD 0, so a single 500 looked like z=30+. I fixed this with a binomial standard-deviation floor for error rate and a Poisson floor (sqrt of median) for request counts. That change was made after seeing test metrics once, so the baseline numbers are mildly optimistic; the floors are theory-derived rather than grid-searched, but treat them with that caveat.
- **Empty minutes are kept** (requests=0) so total outages are visible; a plain GROUP BY would drop them.
- IsolationForest is deliberately left at its default-ish settings; it is under-recalling because contamination 0.01 sets a strict threshold and the training window itself contains ~2% incident minutes. I did not tune it on test data.
- The API trains at startup instead of loading a pickled model, to avoid shipping binary artifacts.

## Limitations
- Synthetic data with clean, strong incidents; real logs have drift, deploys, and gradual degradations.
- Per-minute, no multi-minute smoothing or alert de-duplication.
- Baseline needs training data for each service/hour; unseen services return 422.
- Training data contains incidents (unsupervised, no cleaning), which slightly widens "normal".
- Single seed; no confidence intervals or cross-validation over time.
