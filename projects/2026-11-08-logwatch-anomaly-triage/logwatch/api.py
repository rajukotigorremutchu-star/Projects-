"""FastAPI service: score a one-minute feature window and return triage reasons."""
from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .pipeline import run
from .triage import explain, likely_kind


class Window(BaseModel):
    service: str
    minute: datetime
    requests: int = Field(ge=0)
    error_rate: float = Field(ge=0, le=1)
    p95_ms: float = Field(ge=0)


def create_app(days: int = 6, seed: int = 7) -> FastAPI:
    app = FastAPI(title="logwatch")
    res = run(days, seed)
    app.state.result = res

    @app.get("/health")
    def health():
        return {"status": "ok", "services": res.iforest.services_}

    @app.get("/metrics")
    def metrics():
        return res.metrics

    @app.post("/score")
    def score(w: Window):
        if w.service not in res.iforest.services_:
            raise HTTPException(422, f"unknown service {w.service!r}")
        df = pd.DataFrame([w.model_dump()])
        vals = {"requests": w.requests, "error_rate": w.error_rate, "p95_ms": w.p95_ms}
        z = float(res.baseline.score(df)[0])
        iso = float(res.iforest.score(df)[0])
        reasons = explain(res.baseline, w.service, w.minute.hour, vals)
        flagged = bool(z > res.baseline.threshold or iso > res.iforest.threshold_)
        return {"anomaly": flagged, "baseline_score": round(z, 3), "iforest_score": round(iso, 4),
                "likely_kind": likely_kind(reasons) if flagged else None, "reasons": reasons}

    return app


app = create_app(int(os.environ.get("LOGWATCH_DAYS", 6)), int(os.environ.get("LOGWATCH_SEED", 7)))
