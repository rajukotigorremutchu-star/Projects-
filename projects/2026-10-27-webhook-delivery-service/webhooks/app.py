"""FastAPI surface: manage endpoints, publish events, inspect and replay deliveries."""
import os
import secrets
import time
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from .dispatcher import Dispatcher, HttpxTransport, Transport, publish_event
from .store import Store


class EndpointIn(BaseModel):
    url: str
    event_types: list[str] = Field(default_factory=lambda: ["*"], min_length=1)

    @field_validator("url")
    @classmethod
    def _http_only(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        return v


class EventIn(BaseModel):
    type: str = Field(min_length=1, max_length=100)
    data: dict[str, Any]


def _public(endpoint: dict, include_secret: bool = False) -> dict:
    out = {k: v for k, v in endpoint.items() if k != "secret"}
    if include_secret:
        out["secret"] = endpoint["secret"]
    return out


def create_app(
    store: Optional[Store] = None,
    transport: Optional[Transport] = None,
    clock=time.time,
    **dispatcher_kwargs,
) -> FastAPI:
    store = store or Store(os.environ.get("WEBHOOKS_DB", ":memory:"))
    dispatcher = Dispatcher(store, transport or HttpxTransport(), clock=clock, **dispatcher_kwargs)
    app = FastAPI(title="Webhook Delivery Service", version="1.0.0")
    app.state.store, app.state.dispatcher = store, dispatcher

    @app.post("/endpoints", status_code=201)
    def create_endpoint(body: EndpointIn):
        secret = "whsec_" + secrets.token_hex(24)
        ep = store.add_endpoint(body.url, secret, body.event_types, clock())
        return _public(ep, include_secret=True)  # the only time the secret is shown

    @app.get("/endpoints")
    def list_endpoints():
        return [_public(e) for e in store.list_endpoints()]

    @app.post("/endpoints/{endpoint_id}/disable")
    def disable_endpoint(endpoint_id: str):
        ep = store.set_endpoint_active(endpoint_id, False)
        if not ep:
            raise HTTPException(404, "endpoint not found")
        return _public(ep)

    @app.post("/events")
    def post_event(
        body: EventIn,
        response: Response,
        idempotency_key: Optional[str] = Header(default=None, max_length=255),
    ):
        event, created = publish_event(store, body.type, body.data, clock(), idempotency_key)
        response.status_code = 201 if created else 200
        return {"id": event["id"], "type": event["type"], "duplicate": not created}

    @app.get("/deliveries")
    def list_deliveries(
        status: Optional[str] = Query(default=None, pattern="^(pending|in_flight|succeeded|dead)$"),
        limit: int = Query(default=100, ge=1, le=1000),
    ):
        return store.list_deliveries(status, limit)

    @app.get("/deliveries/{delivery_id}")
    def get_delivery(delivery_id: str):
        d = store.get_delivery(delivery_id)
        if not d:
            raise HTTPException(404, "delivery not found")
        return d

    @app.post("/deliveries/{delivery_id}/replay")
    def replay(delivery_id: str):
        d = store.requeue(delivery_id, clock())
        if not d:
            raise HTTPException(409, "only dead deliveries can be replayed")
        return d

    @app.post("/dispatch")
    def dispatch(limit: int = Query(default=100, ge=1, le=1000)):
        """Run one delivery cycle now. Call from cron/a worker loop (see README)."""
        return dispatcher.run_once(limit).__dict__

    return app
