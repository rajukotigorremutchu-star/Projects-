"""Synthetic HTTP request-log generator with injected, labelled incidents."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SERVICES = {  # name: (requests/min at diurnal peak-ish baseline, median latency ms)
    "checkout": (30.0, 120.0),
    "search": (45.0, 60.0),
    "auth": (20.0, 40.0),
}
INCIDENT_TYPES = ("error_spike", "latency_spike", "traffic_drop")
BASE_ERROR_RATE = 0.01
START = pd.Timestamp("2026-01-05 00:00:00")


@dataclass(frozen=True)
class Incident:
    service: str
    kind: str
    start: pd.Timestamp
    minutes: int


def diurnal(hour_frac: np.ndarray) -> np.ndarray:
    """Traffic multiplier in [0.4, 1.6], peaking mid-afternoon."""
    return 1.0 + 0.6 * np.sin(2 * np.pi * (hour_frac - 9.0) / 24.0)


def plan_incidents(days: int, seed: int, per_service_per_day: int = 2) -> list[Incident]:
    rng = np.random.default_rng(seed + 1)
    out: list[Incident] = []
    for service in SERVICES:
        for day in range(days):
            # one incident per half-day slot so incidents of a service never overlap
            for slot in range(per_service_per_day):
                lo = day * 1440 + slot * (1440 // per_service_per_day) + 30
                hi = lo + (1440 // per_service_per_day) - 90
                start = int(rng.integers(lo, hi))
                out.append(Incident(service, str(rng.choice(INCIDENT_TYPES)),
                                    START + pd.Timedelta(minutes=start),
                                    int(rng.integers(8, 21))))
    return out


def generate(days: int = 6, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame, list[Incident]]:
    """Return (raw logs, per-minute labels, incidents).

    logs:   ts, service, status, latency_ms
    labels: service, minute, is_incident, incident_type
    """
    rng = np.random.default_rng(seed)
    incidents = plan_incidents(days, seed)
    n = days * 1440
    minutes = START + pd.to_timedelta(np.arange(n), unit="m")
    hour_frac = (np.arange(n) % 1440) / 60.0
    frames, label_frames = [], []
    for service, (rate, med_ms) in SERVICES.items():
        traffic = np.full(n, 1.0)
        err = np.full(n, BASE_ERROR_RATE)
        lat = np.full(n, 1.0)
        kind = np.full(n, "", dtype=object)
        for inc in (i for i in incidents if i.service == service):
            a = int((inc.start - START) / pd.Timedelta(minutes=1))
            sl = slice(a, a + inc.minutes)
            kind[sl] = inc.kind
            if inc.kind == "error_spike":
                err[sl] = 0.35
            elif inc.kind == "latency_spike":
                lat[sl] = 5.0
            else:
                traffic[sl] = 0.1
        counts = rng.poisson(rate * diurnal(hour_frac) * traffic)
        idx = np.repeat(np.arange(n), counts)
        ts = minutes[idx] + pd.to_timedelta(rng.uniform(0, 59.99, len(idx)), unit="s")
        lat_ms = rng.lognormal(np.log(med_ms), 0.35, len(idx)) * lat[idx]
        status = np.where(rng.random(len(idx)) < err[idx], 500, 200)
        frames.append(pd.DataFrame({"ts": ts, "service": service, "status": status,
                                    "latency_ms": lat_ms.round(2)}))
        label_frames.append(pd.DataFrame({"service": service, "minute": minutes,
                                          "is_incident": kind != "", "incident_type": kind}))
    logs = pd.concat(frames).sort_values("ts", kind="stable").reset_index(drop=True)
    return logs, pd.concat(label_frames, ignore_index=True), incidents
