# Late-Event Sessionizer (DuckDB)

Incremental clickstream sessionization that handles the messy parts of real event feeds: duplicates, malformed rows, out-of-order arrival and late data.

## Problem
Event batches arrive in order, but events inside them are not. A late event can bridge two sessions, and a very late event should not silently rewrite history. This pipeline decides, per batch, what to accept, reject or divert, and keeps session tables correct without recomputing everything.

## Architecture
```
batch CSV -> stage (all strings) -> validate -> dedupe -> late-data check -> events
                                      |           |            |
                                   rejects     rejects     late_events
events (affected users only) -> window-function sessionization -> sessions
```
- **Validation**: missing event_id/user_id, unparseable timestamp, unknown event_type -> `rejects` with a reason.
- **Dedup**: by `event_id`, within a batch and against all previously seen events (accepted or late).
- **Late policy**: watermark = max accepted `event_ts` at the *start* of the batch. Events older than `watermark - allowed_lateness` go to `late_events` (kept for audit, excluded from sessions).
- **Sessions**: a new session starts when the gap to the user's previous event is strictly greater than `gap_minutes`. Only users touched by the batch are rebuilt, in one transaction (rolled back on error).
- Each batch is processed once (`processed_batches`); re-submitting a batch id raises.

## Setup
```
pip install -r requirements.txt
```
Python 3.10+.

## Usage
```
python -m sessionizer.generate data/sample_batches      # deterministic synthetic data (seed 7)
python -m sessionizer.cli data/sample_batches --out sessions.csv [--db state.duckdb] [--gap 30] [--lateness 60]
pytest
```
The CLI prints one JSON report per batch plus table totals. Sample run on `data/sample_batches` (measured): 191 events accepted, 17 diverted as late, 19 rejects, 41 sessions. Expected sessions are in `data/expected/sessions.csv`.

## Tests (14, all passing)
Gap boundary, validation reasons, in-batch and cross-batch duplicates, batch idempotency guard, late event merging two sessions, too-late diversion, affected-user-only rebuild, row-conservation per batch, a sample-data golden file, and a property check on 3 generated datasets that **incremental sessions == full rebuild from accepted events**.

The golden file was produced by this code, so it is a regression guard, not independent ground truth; correctness rests on the targeted tests and the incremental-vs-full invariant.

## Design decisions
- Watermark taken from before the batch so a batch cannot declare its own events late.
- Late events are diverted rather than merged: sessions are stable once outside the lateness window.
- Rebuilding per affected user is simple and correct; DuckDB makes it cheap.

## Limitations
- Late events are not reprocessed later if the lateness setting changes.
- Batch order is by filename; no streaming source.
- Timestamps are naive (no timezone handling).
- Unique-id tracking holds all ids in memory/table; no TTL.
- Synthetic data only; no performance benchmarking was done.
