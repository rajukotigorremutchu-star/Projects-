from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import Body, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .evaluator import evaluate
from .models import Decision, FlagDefinition
from .store import FlagStore, VersionConflict


class BulkRequest(BaseModel):
    keys: Optional[list[str]] = None  # None = every flag
    context: dict[str, Any] = Field(default_factory=dict)


def create_app(store: Optional[FlagStore] = None) -> FastAPI:
    store = store or FlagStore(os.environ.get("FLAGS_DB", ":memory:"))
    app = FastAPI(title="Feature Flag Service")
    app.state.store = store

    @app.put("/flags/{key}")
    def put_flag(key: str, definition: FlagDefinition,
                 if_match: Optional[int] = Header(default=None, alias="If-Match")):
        try:
            version = store.put(key, definition, expected_version=if_match)
        except VersionConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return {"key": key, "version": version}

    @app.get("/flags")
    def list_flags():
        return [{"version": v, **f.model_dump()} for f, v in store.list()]

    @app.get("/flags/{key}")
    def get_flag(key: str):
        found = store.get(key)
        if not found:
            raise HTTPException(404, "flag not found")
        return {"version": found[1], **found[0].model_dump()}

    @app.delete("/flags/{key}", status_code=204)
    def delete_flag(key: str):
        if not store.delete(key):
            raise HTTPException(404, "flag not found")

    @app.get("/flags/{key}/history")
    def history(key: str):
        h = store.history(key)
        if not h:
            raise HTTPException(404, "no history for flag")
        return h

    @app.post("/evaluate/{key}", response_model=Decision)
    def evaluate_one(key: str, context: dict[str, Any] = Body(default_factory=dict)):
        found = store.get(key)
        if not found:
            # Fail closed with an explicit reason instead of an error: callers
            # shouldn't crash because a flag was deleted.
            return Decision(key=key, value=False, reason="missing_flag")
        return evaluate(found[0], context)

    @app.post("/evaluate", response_model=dict[str, Decision])
    def evaluate_bulk(req: BulkRequest):
        flags = {f.key: f for f, _ in store.list()}
        keys = req.keys if req.keys is not None else sorted(flags)
        return {k: evaluate(flags[k], req.context) if k in flags
                else Decision(key=k, value=False, reason="missing_flag") for k in keys}

    return app


app = create_app()
