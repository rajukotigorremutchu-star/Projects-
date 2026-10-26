# SCD Type 2 Snapshot Pipeline (DuckDB)

Data engineering project: turn daily **full snapshots** of a source table into a
**Type 2 slowly changing dimension** with full history, point-in-time queries, and
built-in data-quality checks. Pure SQL in DuckDB driven by a small Python CLI.

## Problem
Source systems often expose only "today's state" of a table. Analysts need to know
what a customer's plan or city was *on a past date*. This pipeline derives history
by diffing each snapshot against the current dimension rows.

## Architecture
```
snapshot_YYYY-MM-DD.csv ──> read_snapshot (validate keys)
                              └─> stg_h (temp table, row hash)
                                    ├─ UPDATE dim_customer: close changed/missing current rows
                                    └─ INSERT dim_customer: new / changed / re-appearing keys
                                  (one transaction per snapshot; load_log records the run)
dim_customer ──> quality_checks(), as_of(date), export_dimension(csv)
```
- `scd2/pipeline.py` – loader, quality checks, as-of query, export
- `scd2/generate.py` – seeded synthetic snapshot generator
- `scd2/cli.py` – `generate` and `load` commands
- Intervals are half-open: `valid_from <= d < valid_to`; open rows use `9999-12-31`.

## Setup
```
pip install -r requirements.txt
```
Python 3.10+.

## Usage
```
python -m scd2.cli load data/sample --export out/dim.csv --as-of 2026-01-03
python -m scd2.cli generate out/snaps --days 30 --customers 200
python -m scd2.cli load out/snaps --db out/warehouse.duckdb
pytest
```
`load` exits non-zero if any quality check reports violations.

## Sample data
`data/sample/` holds 4 hand-written snapshots exercising: attribute change (customer 1),
new key (4), deletion then re-appearance (3), and unchanged rows. The expected result is
`data/expected_dim_customer.csv` (derived by hand, then confirmed to match the pipeline;
the test suite asserts byte equality).

## Quality checks
No multiple current rows per key; no overlapping intervals; no empty/inverted intervals;
`is_current` consistent with open end date; no adjacent identical versions.
A property test also verifies that `as_of(d)` equals the raw snapshot for every day
of a generated 15-day history.

## Design decisions
- **Change detection by hash** of tracked columns, with length-prefixed NULL-aware
  encoding (a first version used `coalesce(col,'<null>')`; a test caught that it collided
  with a literal `'<null>'` string, so it was replaced).
- **Idempotent**: reloading the latest date is a no-op. **Out-of-order** dates raise,
  because incremental SCD2 cannot rewrite earlier history.
- **Missing key = closed**, re-appearance opens a new version (gap preserved).
- Each snapshot is applied in a transaction; failures roll back.

## Limitations
- Granularity is the snapshot date; intra-day changes are invisible.
- No late-arriving/backfilled snapshots (would need a rebuild from raw files).
- Single dimension (`customer`) with hard-coded tracked columns.
- Quality-check overlap query is O(n²) per key, fine for demos, not for huge dimensions.
- Only synthetic data was tested; no performance benchmarks were run.
