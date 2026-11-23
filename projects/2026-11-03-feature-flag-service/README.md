# Feature Flag Service

A small feature-flag / experimentation service: targeting rules, sticky percentage rollouts, multivariate A/B splits, versioned storage with audit history, and a FastAPI HTTP API. Runs locally with SQLite; no external services.

## Problem
Teams need to ship code dark and enable it for specific users or a growing percentage of traffic, without redeploying. The hard parts are correctness details: users must get the *same* answer every time, raising a rollout from 10% to 40% must not flip anyone off, and a rule must never apply to users you know nothing about.

## Architecture
```
flagsvc/models.py     pydantic models + validation (Flag, Rule, Condition, Decision)
flagsvc/evaluator.py  pure function evaluate(flag, context) -> Decision   (no I/O)
flagsvc/store.py      SQLite store: optimistic versioning, audit history
flagsvc/api.py        FastAPI app (CRUD, history, single + bulk evaluation)
scripts/run_sample.py evaluates samples/flags.json x samples/contexts.json
tests/                52 tests (evaluator semantics, store, API, sample output)
```
Evaluation order: disabled → `off_value`; otherwise first rule whose conditions all match (AND) **and** whose rollout bucket admits the user wins; else `default_value`.

## Setup
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python scripts/run_sample.py            # print decisions
python scripts/run_sample.py --check    # compare with samples/expected_output.json
FLAGS_DB=flags.db uvicorn flagsvc.api:app --port 8000   # omit FLAGS_DB for in-memory
```

## Usage
```bash
curl -X PUT localhost:8000/flags/new-checkout -H 'content-type: application/json' \
     -d @<(python -c "import json;print(json.dumps(json.load(open('samples/flags.json'))['new-checkout']))")
curl -X POST localhost:8000/evaluate/new-checkout -H 'content-type: application/json' \
     -d '{"user_id":"u1","email":"ann@example.com"}'
# {"key":"new-checkout","value":true,"reason":"rule_match","rule_index":0}
```
Endpoints: `PUT/GET/DELETE /flags/{key}`, `GET /flags`, `GET /flags/{key}/history`, `POST /evaluate/{key}` (body = context), `POST /evaluate` (`{"keys": [...], "context": {...}}`). `PUT` accepts `If-Match: <version>` (`0` = must not exist) and returns 409 on mismatch.

Operators: `eq neq in not_in gt gte lt lte contains starts_with ends_with`.
The context attribute `user_id` is the bucketing subject.

Sample flags, contexts, and the resulting decisions are in `samples/` (`flags.json`, `contexts.json`, `expected_output.json`).

## Design decisions
- **Hash bucketing**: `sha256(key:salt:purpose:user_id)` → 0.01% buckets. Stateless and sticky; `purpose` keeps the rollout bucket independent from the A/B split bucket; `salt` re-randomizes an experiment.
- **Monotonic rollouts**: admit when `bucket < rollout`, so increasing the percentage only adds users (tested).
- **Missing attributes never match**, even for `neq`/`not_in`, and a partial rollout excludes requests with no `user_id`.
- **Strict equality**: `True` never equals `1`.
- **Fail closed**: evaluating an unknown flag returns `false` with reason `missing_flag` rather than an error.
- **Evaluator is pure**, so it is trivially testable and could be embedded as a library/SDK.
- **Optimistic concurrency** via version + `If-Match` avoids lost updates between editors.

## Test results
`pytest -q` → 52 passed (run locally with Python 3.11, fastapi 0.115.6, pytest 8.3.4). Distribution tests (rollout ≈30% ±3pts over 5000 ids; 70/30 split ±4pts over 3000 ids) use fixed ids and a deterministic hash, so they are not flaky.

## Limitations
- No authentication, authorization, or rate limiting — do not expose publicly as is.
- Flags are read from SQLite per request; no caching, streaming/SSE updates, or SDK-side local evaluation.
- Single-process SQLite with a global lock; not horizontally scalable.
- No rule nesting (OR groups), segments, semver comparison, or scheduled changes.
- Rollback = re-PUT an old definition from `/history`; there is no one-click revert endpoint.
- Hash distribution is verified statistically on small samples, not formally.
