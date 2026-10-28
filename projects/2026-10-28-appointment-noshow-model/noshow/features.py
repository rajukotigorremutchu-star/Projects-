"""Leakage-safe feature engineering: history uses only strictly earlier appointments."""
import pandas as pd

NUMERIC = ["age", "lead_days", "hour", "sms_reminder", "dow",
           "prior_appts", "prior_noshow_rate"]
TARGET = "no_show"


def add_features(df: pd.DataFrame, prior_strength: float = 2.0, global_rate: float | None = None) -> pd.DataFrame:
    """Add day-of-week and per-patient history features.

    prior_noshow_rate is a smoothed rate over the patient's *earlier* appointments
    (shift(1) before cumulative sums), so a row never sees its own label or later ones.
    `global_rate` should be computed from training data only.
    """
    df = df.sort_values(["date", "patient_id"]).reset_index(drop=True).copy()
    if global_rate is None:
        global_rate = float(df[TARGET].mean())
    g = df.groupby("patient_id")[TARGET]
    prior_n = g.cumcount()  # number of earlier rows for this patient
    prior_ns = g.cumsum() - df[TARGET]
    df["prior_appts"] = prior_n
    df["prior_noshow_rate"] = (prior_ns + prior_strength * global_rate) / (prior_n + prior_strength)
    df["dow"] = df["date"].dt.day_of_week
    return df
