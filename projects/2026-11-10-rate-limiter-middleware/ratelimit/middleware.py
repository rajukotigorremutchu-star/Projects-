"""Pure-ASGI rate limit middleware (works with FastAPI/Starlette)."""
import json
import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from .algorithms import Limiter


def client_ip_key(scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


@dataclass
class Rule:
    path_prefix: str
    limiter: Limiter
    cost: int = 1
    name: Optional[str] = None


class RateLimitMiddleware:
    """First rule whose path_prefix matches applies. Unmatched paths are unlimited.

    Key function defaults to client IP; pass `key_func(scope)->str` to key by API key etc.
    Fails open: if the limiter raises, the request is let through.
    """

    def __init__(self, app, rules: list, key_func: Callable = client_ip_key,
                 exempt_paths=("/health",)):
        self.app, self.rules, self.key_func = app, rules, key_func
        self.exempt = set(exempt_paths)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in self.exempt:
            return await self.app(scope, receive, send)
        rule = next((r for r in self.rules if scope["path"].startswith(r.path_prefix)), None)
        if rule is None:
            return await self.app(scope, receive, send)
        try:
            d = rule.limiter.check(f"{rule.name or rule.path_prefix}:{self.key_func(scope)}", rule.cost)
        except Exception:
            return await self.app(scope, receive, send)

        def headers():
            h = [(b"ratelimit-limit", str(d.limit).encode()),
                 (b"ratelimit-remaining", str(d.remaining).encode()),
                 (b"ratelimit-reset", str(math.ceil(d.reset_after)).encode())]
            return h

        if d.allowed:
            async def send_wrapped(msg):
                if msg["type"] == "http.response.start":
                    msg = {**msg, "headers": list(msg.get("headers", [])) + headers()}
                await send(msg)
            return await self.app(scope, receive, send_wrapped)

        retry = "never" if math.isinf(d.retry_after) else str(max(1, math.ceil(d.retry_after)))
        body = json.dumps({"detail": "rate limit exceeded", "retry_after": retry}).encode()
        h = headers() + [(b"content-type", b"application/json"),
                         (b"content-length", str(len(body)).encode())]
        if retry != "never":
            h.append((b"retry-after", retry.encode()))
        await send({"type": "http.response.start", "status": 429, "headers": h})
        await send({"type": "http.response.body", "body": body})
