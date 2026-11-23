"""FastAPI service that serves predictions and logs them to SQLite."""
from __future__ import annotations

import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .model import predict

DEFAULT_MODEL = "artifacts/model.joblib"
DEFAULT_DB = "artifacts/tickets.db"


class Ticket(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class Batch(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=100)


def create_app(model_path: str | None = None, db_path: str | None = None, low_confidence: float = 0.5) -> FastAPI:
    model_path = model_path or os.environ.get("TRIAGE_MODEL", DEFAULT_MODEL)
    db_path = db_path or os.environ.get("TRIAGE_DB", DEFAULT_DB)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not Path(model_path).exists():
            raise RuntimeError(f"model not found at {model_path}; run `python -m triage.cli train` first")
        app.state.model = joblib.load(model_path)
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS predictions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " text TEXT, label TEXT, confidence REAL, needs_review INTEGER)"
            )
        yield

    app = FastAPI(title="Ticket Triage", lifespan=lifespan)

    def _run(texts: list[str]) -> list[dict]:
        cleaned = [" ".join(t.split()) for t in texts]
        if any(not t for t in cleaned):
            raise HTTPException(422, "texts must not be blank")
        preds = predict(app.state.model, cleaned)
        with sqlite3.connect(db_path) as con:
            for t, p in zip(cleaned, preds):
                p["needs_review"] = p["confidence"] < low_confidence
                con.execute(
                    "INSERT INTO predictions (text,label,confidence,needs_review) VALUES (?,?,?,?)",
                    (t, p["label"], p["confidence"], int(p["needs_review"])),
                )
        return preds

    @app.get("/health")
    def health():
        return {"status": "ok", "classes": [str(c) for c in app.state.model.classes_]}

    @app.post("/predict")
    def predict_one(ticket: Ticket):
        return _run([ticket.text])[0]

    @app.post("/predict/batch")
    def predict_batch(batch: Batch):
        return {"predictions": _run(batch.texts)}

    @app.get("/stats")
    def stats():
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT label, COUNT(*), SUM(needs_review) FROM predictions GROUP BY label ORDER BY label"
            ).fetchall()
        return {"by_label": {l: {"count": c, "needs_review": int(r or 0)} for l, c, r in rows}}

    return app


def app_factory() -> FastAPI:  # for `uvicorn triage.api:app_factory --factory`
    return create_app()
