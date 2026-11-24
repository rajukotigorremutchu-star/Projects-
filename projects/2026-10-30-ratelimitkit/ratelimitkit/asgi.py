"""Pure-ASGI rate-limit middleware (works with FastAPI/Starlette)."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Callable, Iterable

from starlette.concurrency import run_in_threadpool

from .algorithms import Decision
from .limiter import Limiter


def client_key(scope) -> str:
    """API key header if present, else the client IP."""
    for name, value in scope.get("headers", []):
        if name == b"x-api-key":
            return "key:" + value.decode("latin-1")
    client = scope.get("client")
    return "ip:" + (client[0] if client else "unknown")


@dataclass
class Rule:
    """Applies ``limiter`` to requests whose path starts with ``path_prefix``."""

    path_prefix: str
    limiter: Limiter
    methods: frozenset[str] | None = None
    cost: int = 1

    def matches(self, scope) -> bool:
        if not scope["path"].startswith(self.path_prefix):
            return False
        return self.methods is None or scope["method"] in self.methods


def _headers(d: Decision) -> list[tuple[bytes, bytes]]:
    out = [
        (b"ratelimit-limit", str(d.limit).encode()),
        (b"ratelimit-remaining", str(d.remaining).encode()),
        (b"ratelimit-reset", str(math.ceil(d.reset_after)).encode()),
    ]
    if not d.allowed and d.retry_after is not None:
        out.append((b"retry-after", str(max(1, math.ceil(d.retry_after))).encode()))
    return out


class RateLimitMiddleware:
    """The first matching rule wins; unmatched requests pass through untouched.

    ``fail_open=True`` lets traffic through when the store raises, so a broken
    limiter backend degrades to "no limiting" instead of an outage.
    """

    def __init__(self, app, rules: Iterable[Rule],
                 key_func: Callable[[dict], str] = client_key, fail_open: bool = True):
        self.app = app
        self.rules = list(rules)
        self.key_func = key_func
        self.fail_open = fail_open

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rule = next((r for r in self.rules if r.matches(scope)), None)
        if rule is None:
            return await self.app(scope, receive, send)

        try:
            decision = await run_in_threadpool(rule.limiter.hit, self.key_func(scope), rule.cost)
        except Exception:
            if not self.fail_open:
                raise
            return await self.app(scope, receive, send)

        if not decision.allowed:
            body = json.dumps({"detail": "rate limit exceeded",
                               "retry_after": decision.retry_after}).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode()),
                                    *_headers(decision)]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                message = {**message, "headers": [*message.get("headers", []), *_headers(decision)]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
