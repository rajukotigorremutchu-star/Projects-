# Appointment No-Show Prediction (leakage-safe, temporal split)

## Problem
Clinics lose capacity to patients who miss appointments. Predict `no_show` for an appointment using information available **before** it happens, and compare simple models against a baseline. The focus is evaluation hygiene, not leaderboard chasing.

## Data
Fully synthetic (`noshow/data.py`, seeded, no real patient data). Each patient has a latent reliability; lead time, reminder SMS, Monday, late-afternoon slots and age also affect risk. `sample/appointments_sample.csv` is a 60-row example of the input format.

## Architecture
- `noshow/data.py` – synthetic generator.
- `noshow/features.py` – `day_of_week` plus per-patient history (`prior_appts`, smoothed `prior_noshow_rate`) computed only from strictly earlier appointments (cumulative sum minus the row's own label).
- `noshow/train.py` – temporal split (last 25% of dates = test), three models, metrics JSON.

## Setup & usage
```bash
pip install -r requirements.txt
python -m pytest -q
python -m noshow.train --out metrics.json          # synthetic data, seed 42
python -m noshow.train --input my.csv              # columns: patient_id,date,age,lead_days,hour,sms_reminder,no_show
```

## Measured results (seed 42, 3904 train / 1308 test rows; reproduced in `sample/expected_metrics.json`)
| Model | ROC-AUC | PR-AUC | Brier |
|---|---|---|---|
| Baseline (train prior) | 0.500 | 0.276 | 0.2007 |
| Logistic regression | 0.633 | 0.390 | 0.1915 |
| HistGradientBoosting | 0.618 | 0.370 | 0.1956 |

PR-AUC is reported because no-shows are the minority class (test rate 27.6%); Brier score checks probability quality. Exact numbers may vary slightly across library versions.

## Design decisions
- **Temporal split**, not random: a random split would let a patient's future appointments inform predictions about their past.
- **Leakage controls**: history features exclude the row's own label and later rows; the smoothing rate comes from training data only. Tests verify both (flipping future labels does not change earlier features).
- **Baseline** predicts the training prior, so any model must beat constant risk.
- Shallow boosting with fixed hyperparameters; no tuning on the test set.

## Limitations
- Data is synthetic and the signal is mostly linear, so logistic regression beating boosting says nothing about real clinics.
- No probability calibration step, no hyperparameter search, no confidence intervals (single split, single seed).
- Patients first seen in the test period have no history (they fall back to the prior rate).
