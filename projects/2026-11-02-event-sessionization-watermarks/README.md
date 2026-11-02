# Event Sessionization with Watermarks (stdlib + SQLite)

## Problem
Clickstream events arrive out of order, duplicated, malformed, or very late. Batch jobs that
"group by user and gap" either wait forever or silently miscount. This project implements the
core of a streaming-style sessionizer as an idempotent micro-batch pipeline: it decides *when a
session is final*, what to do with *late* data, and makes re-runs safe.

## Architecture
```
JSONL / generator -> micro-batches -> [validate -> dedupe -> late check -> buffer]
                                          |                          |
                                    dead_letters               finalize via watermark
                                                                     |
                                                               sessions table
```
All state (buffer, seen ids, max event time, processed batch ids, output, dead letters) lives in
SQLite and is committed in **one transaction per batch**, so a crash never leaves partial state.

Rules (`sessionizer/pipeline.py`):
- Session = a user's events with consecutive gaps <= `gap` seconds.
- Watermark = max event_time from *previous* batches - `allowed_lateness`.
- event_time < watermark on arrival -> `late` dead letter. Seen event_id -> `duplicate`. Missing id/user or non-integer time -> `invalid`.
- A buffered session is finalized when `end + gap < watermark`. `flush()` finalizes the rest at end of stream.
- A batch id already processed is skipped (idempotent replay).

## Setup & usage
```
pip install -r requirements.txt     # only pytest; the pipeline is stdlib-only
python -m sessionizer.cli                                   # synthetic data (seed 7)
python -m sessionizer.cli --input data/sample_events.jsonl --gap 100 --lateness 50 --db out.db
python -m pytest -q
```
`data/sample_events.jsonl` + `data/expected_sessions.json` are a hand-derived input/expected-output pair
(checked by a test). In the sample: `e3` is a duplicate, and `e6`/`old` arrive after the watermark (1950) so are dead-lettered.

## Measured results (synthetic, seed 7, gap 1800s)
Dead letters by allowed lateness (15 sessions produced in each case):

| lateness | late | duplicate | invalid |
|---|---|---|---|
| 0s | 20 | 4 | 2 |
| 600s | 14 | 4 | 2 |
| 1200s | 10 | 4 | 2 |

Larger lateness drops fewer events at the cost of delaying session finalization. Tests: 11 passed.

## Design decisions
- Watermark from *prior* batches only, so output doesn't depend on order within a batch.
- Late events still enter `seen_events`, so a replayed late event is classed consistently.
- Sessions recomputed from the per-user buffer on each batch: simple and obviously correct, O(buffer) per batch.

## Limitations
- The watermark is global, not per user/partition; a lagging user's events are judged against other users' progress (visible in the synthetic numbers above).
- `seen_events` grows without bound (no TTL/compaction).
- Late events are dropped, not used to correct already-emitted sessions.
- Integer epoch seconds only; no timezone handling; not tuned for large volumes.
