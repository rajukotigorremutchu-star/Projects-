"""Reference consumer: a FastAPI app that verifies signatures and de-duplicates by Webhook-Id."""
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request

from . import signing


def create_receiver(secret: str, clock=time.time, fail_first: int = 0) -> FastAPI:
    """`fail_first` makes the first N valid requests return 503 (to demo retries)."""
    app = FastAPI()
    app.state.processed = {}  # Webhook-Id -> body; makes handling idempotent under retries
    state = {"failures_left": fail_first}

    @app.post("/hook")
    async def hook(
        request: Request,
        webhook_id: str = Header(),
        webhook_timestamp: str = Header(),
        webhook_signature: str = Header(),
    ):
        body = await request.body()
        try:
            ts: Optional[int] = int(webhook_timestamp)
        except ValueError:
            ts = None
        if ts is None or not signing.verify(secret, webhook_id, ts, body, webhook_signature, clock()):
            raise HTTPException(401, "bad signature")
        if state["failures_left"] > 0:
            state["failures_left"] -= 1
            raise HTTPException(503, "temporarily unavailable")
        app.state.processed.setdefault(webhook_id, body)
        return {"ok": True}

    return app
