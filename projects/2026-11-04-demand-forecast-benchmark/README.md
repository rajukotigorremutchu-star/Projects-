# Demand Forecast Benchmark (leakage-safe, 7-day-ahead)

Machine-learning project: forecast daily demand per series 7 days ahead and
compare learned models against honest baselines using a time-based split.

## Problem
Retail-style demand planning forecasts day *t* one week ahead. The common
failure is leakage: lag/rolling features that peek at days after the forecast
origin, or random train/test splits that mix future into training. This project
builds the pipeline so those mistakes are structurally prevented and tested.

## Architecture
```
demandcast/data.py       seeded synthetic generator (trend, weekly/yearly seasonality, promos, Poisson noise)
demandcast/features.py   direct h-step features: lags >= h, rolling means ending at origin (shift(h) then roll)
demandcast/metrics.py    MAE, WAPE, MASE scale (seasonal-naive in-sample MAE)
demandcast/benchmark.py  temporal split, baselines, Ridge, HistGradientBoosting (Poisson loss)
demandcast/cli.py        `generate` and `evaluate` commands
```
Split (by date, never random): train | validation (28 d) | test (last 56 d).
Validation WAPE is reported for model selection; final models are refit on
train+val and scored once on test. Test rows never enter fitting.

## Setup & usage
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m demandcast.cli generate --out /tmp/demand.csv            # 12 series x 540 days
python -m demandcast.cli evaluate --data /tmp/demand.csv --json /tmp/res.json
python -m demandcast.cli evaluate --data samples/demand_sample.csv  # matches samples/expected_output.csv
```
Input CSV columns: `series_id,date,promo,y`.

## Measured results (test = last 56 days, seed 0)
Small sample (`samples/demand_sample.csv`, 4 series x 300 days; `samples/expected_output.csv`):

| model | MAE | WAPE | MASE |
|---|---|---|---|
| seasonal_naive_7 (baseline) | 19.616 | 0.1297 | 0.8732 |
| moving_avg_28 | 37.736 | 0.2496 | 1.6799 |
| ridge | 15.599 | 0.1032 | 0.6944 |
| hist_gbm | 13.735 | 0.0908 | 0.6114 |

Default data (12 series x 540 days, seed 42; `samples/expected_output_full.csv`):
seasonal_naive WAPE 0.1838, moving_avg_28 0.2580, ridge 0.1229, hist_gbm 0.0892.
These come from one synthetic dataset and one seed; they illustrate the
pipeline and are not evidence of real-world performance.

## Design decisions
- **Direct forecasting with lags ≥ horizon**: `build_features` raises if a lag is shorter than the horizon.
- **Leakage test**: corrupting all `y` after the origin must not change a row's lag/rolling features.
- **Metrics**: WAPE (scale-free, robust to zeros in aggregate) and MASE vs. the seasonal-naive in-sample scale; MAE for units.
- **Poisson loss** for the GBM suits non-negative count demand; predictions clipped at 0.
- Models fit only on dates before the validation/test window; rows with missing lag history are dropped.

## Limitations
- Synthetic data: results depend on the generator's seasonality and noise.
- Single split, single seed; no rolling-origin cross-validation or confidence intervals.
- Hyperparameters are fixed (validation WAPE is reported but not used to search).
- Point forecasts only; no prediction intervals, no hierarchy or cold-start handling.
- Promo flags are assumed known in advance.
