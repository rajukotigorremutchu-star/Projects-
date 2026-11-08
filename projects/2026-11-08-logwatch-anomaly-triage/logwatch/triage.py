"""Human-readable reasons for a flagged minute, from the baseline's robust z-scores."""
from __future__ import annotations

from .detect import METRICS, RobustZBaseline

LABELS = {"requests": "request volume", "error_rate": "error rate", "p95_ms": "p95 latency"}


def explain(baseline: RobustZBaseline, service: str, hour: int, values: dict[str, float], top: float = 4.0) -> list[str]:
    reasons = []
    zs = baseline.z_scores(service, hour, values)
    for m in sorted(METRICS, key=lambda k: -abs(zs[k])):
        if abs(zs[m]) >= top:
            med = baseline.stats_[(service, hour, m)][0]
            direction = "above" if zs[m] > 0 else "below"
            reasons.append(f"{LABELS[m]} {values[m]:.4g} is {direction} typical {med:.4g} for this hour (robust z={zs[m]:+.1f})")
    return reasons


def likely_kind(reasons: list[str]) -> str:
    if not reasons:
        return "unknown"
    first = reasons[0]
    if first.startswith("error rate"):
        return "error_spike"
    if first.startswith("p95"):
        return "latency_spike"
    return "traffic_drop" if "below" in first else "traffic_surge"
