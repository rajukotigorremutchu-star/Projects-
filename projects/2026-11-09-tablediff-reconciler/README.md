# tablediff — checksum-bisection table reconciler (DuckDB)

## Problem
After a migration or ETL load you need to know *exactly* which rows differ between a source and a target table. A full outer join works but reads every value on both sides; in a real warehouse or cross-database copy that is expensive. `tablediff` is a small, local implementation of the "data-diff" idea: compare cheap checksums of key ranges, discard ranges that match, and only join the rows in ranges that don't.

## Architecture
```
CSV ─► DuckDB tables (all VARCHAR) ─► _h_<table>(k, row_hash)
                                          │
        walk(lo,hi): (count, sum(hash+k)) equal on both sides? ─yes─► prune
                                          │no
                 small enough (≤ threshold rows) ─► FULL OUTER JOIN detail
                                          │no
                              split range at midpoint, recurse
```
- `tablediff/core.py` – normalisation, hashing, bisection, row-level diff, `DiffResult`.
- `tablediff/cli.py` – CLI, JSON report, exit codes (0 identical, 1 differences, 2 invalid input).
- `scripts/make_sample.py` – deterministic sample data (`data/source.csv`, `data/target.csv`).
- `data/expected_report.json` – the report the CLI produces for the sample (checked by a test).

## Setup
```
pip install -r requirements.txt     # duckdb, pytest; Python 3.11 used
```

## Usage
```
python -m tablediff data/source.csv data/target.csv --key order_id --ignore updated_at --threshold 16 --out report.json
```
Options: `--ignore COL...`, `--threshold N` (rows per mismatching segment before splitting, default 64), `--precision N` (decimals for numeric comparison, default 6), `--no-numeric-normalize`, `--no-trim`.

The sample plants: a status change (order 10), an amount change (121), a whitespace-only difference (122) and a `12.30` vs `12.300` difference (201) which are both treated as equal, three rows missing in target (251–253), one extra (999), and an ETL-touched `updated_at` column that is ignored. Report: 3 missing, 1 extra, 2 changed, 295 identical.

Tests: `python -m pytest -q`

## Design decisions
- **Order-independent aggregate** (`count`, `sum(hash + key)`): no sorting needed; adding the key stops two rows swapping values across keys from cancelling.
- **Canonical normalisation before hashing and before comparison** (same expression for both), so a range is "equal" iff its rows would be reported equal. NULL, empty string and the literal text `NULL` are distinct.
- **Everything loaded as VARCHAR**: avoids CSV type inference differing between the two files (e.g. `10` vs `10.0`); numeric-looking values are compared as rounded doubles.
- **Randomised test vs a naive Python oracle** guards the pruning logic; another test asserts bisection reads far fewer rows than a full join.

## Limitations
- Single, unique, integer key (bisection splits numeric ranges). Composite/text keys aren't supported; duplicate or non-integer keys are rejected.
- Hash is 64-bit sum-based, so a collision could in theory hide a difference (not observed in tests, but not proven impossible).
- Numeric normalisation goes through DOUBLE: integers beyond 2^53 lose precision, and strings like `007` vs `7` compare equal (use `--no-numeric-normalize`).
- Sparse key spaces cause deeper bisection; the benefit shown is on rows read within DuckDB, not measured on a networked database. I did not benchmark wall-clock time.
- CSV only; both tables are loaded fully into an in-memory DuckDB.
