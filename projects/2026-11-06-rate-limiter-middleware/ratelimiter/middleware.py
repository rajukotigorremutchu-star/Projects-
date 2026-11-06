"""Pure-ASGI rate limiting middleware (works with FastAPI/Starlette)."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from .algorithms import Clock, Limiter, make_limiter


@dataclass
class Rule:
    """Apply `limiter` to requests whose path starts with `prefix` (longest prefix wins)."""
    prefix: str
    limiter: Limiter


def default_key(scope) -> str:
    """API key header if present, otherwise client IP."""
    for k, v in scope.get("headers", []):
        if k == b"x-api-key":
            return "key:" + v.decode("latin-1")
    client = scope.get("client")
    return "ip:" + (client[0] if client else "unknown")


class RateLimitMiddleware:
    def __init__(self, app, rules: List[Rule], key_func: Callable = default_key,
                 exempt_paths: tuple = ("/health",)):
        self.app = app
        self.rules = sorted(rules, key=lambda r: len(r.prefix), reverse=True)
        self.key_func = key_func
        self.exempt = exempt_paths

    def _match(self, path: str) -> Optional[Rule]:
        return next((r for r in self.rules if path.startswith(r.prefix)), None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt:
            return await self.app(scope, receive, send)
        rule = self._match(scope["path"])
        if rule is None:
            return await self.app(scope, receive, send)

        # Limiters are scoped per rule, so the same client has independent quotas per rule.
        d = rule.limiter.check(f"{rule.prefix}|{self.key_func(scope)}")
        headers = [
            (b"x-ratelimit-limit", str(d.limit).encode()),
            (b"x-ratelimit-remaining", str(d.remaining).encode()),
            (b"x-ratelimit-reset", str(math.ceil(d.reset_after)).encode()),
        ]
        if d.allowed:
            async def send_with_headers(msg):
                if msg["type"] == "http.response.start":
                    msg = {**msg, "headers": list(msg.get("headers", [])) + headers}
                await send(msg)
            return await self.app(scope, receive, send_with_headers)

        retry = math.ceil(d.retry_after) if math.isfinite(d.retry_after) else 0
        body = json.dumps({"detail": "rate limit exceeded", "retry_after": retry}).encode()
        headers += [(b"retry-after", str(retry).encode()),
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode())]
        await send({"type": "http.response.start", "status": 429, "headers": headers})
        await send({"type": "http.response.body", "body": body})


def rule(prefix: str, algorithm: str, limit: int, window: float, clock: Clock = time.monotonic) -> Rule:
    return Rule(prefix, make_limiter(algorithm, limit, window, clock))
