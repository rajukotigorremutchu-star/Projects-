"""Synthetic clinic appointment generator (no real patient data)."""
import numpy as np
import pandas as pd


def generate(n_patients=800, n_days=365, seed=42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # latent per-patient reliability drives repeat behaviour (history features can learn it)
    patient_risk = rng.beta(2, 6, n_patients)
    rows = []
    start = pd.Timestamp("2025-01-01")
    for pid in range(n_patients):
        n_appt = rng.integers(2, 12)
        days = np.sort(rng.choice(n_days, n_appt, replace=False))
        age = int(rng.integers(5, 90))
        for d in days:
            lead = int(rng.integers(0, 45))
            hour = int(rng.integers(8, 17))
            sms = int(rng.random() < 0.7)
            date = start + pd.Timedelta(days=int(d))
            logit = (-1.6 + 4.0 * (patient_risk[pid] - 0.25) + 0.03 * lead
                     - 0.5 * sms + (0.4 if date.day_of_week == 0 else 0)
                     + (0.3 if hour >= 15 else 0) - 0.01 * (age - 40))
            p = 1 / (1 + np.exp(-logit))
            rows.append((pid, date, age, lead, hour, sms, int(rng.random() < p)))
    df = pd.DataFrame(rows, columns=["patient_id", "date", "age", "lead_days",
                                     "hour", "sms_reminder", "no_show"])
    return df.sort_values(["date", "patient_id"]).reset_index(drop=True)
