"""Delivery engine: sends due deliveries, classifies outcomes, schedules retries."""
import json
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import httpx

from . import signing
from .backoff import compute_delay
from .store import Store, new_id

# Client-error responses that mean "try again later". Other 3xx/4xx are permanent.
RETRYABLE_4XX = {408, 409, 425, 429}


@dataclass
class SendResult:
    status_code: int
    retry_after: Optional[float] = None


class TransportError(Exception):
    """Network-level failure (DNS, connect, timeout, TLS)."""


class Transport(Protocol):
    def send(self, url: str, headers: dict, body: bytes, timeout: float) -> SendResult: ...


class HttpxTransport:
    def __init__(self, client: Optional[httpx.Client] = None):
        # follow_redirects stays off: a 3xx is not a success and should not hop hosts silently.
        self._client = client or httpx.Client(follow_redirects=False)

    def send(self, url: str, headers: dict, body: bytes, timeout: float) -> SendResult:
        try:
            resp = self._client.post(url, content=body, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TransportError(f"{type(exc).__name__}: {exc}") from exc
        retry_after = None
        raw = resp.headers.get("retry-after")
        if raw:
            try:
                retry_after = float(raw)
            except ValueError:
                pass  # HTTP-date form is not supported; fall back to normal backoff
        return SendResult(resp.status_code, retry_after)


@dataclass
class DispatchStats:
    attempted: int = 0
    succeeded: int = 0
    retried: int = 0
    dead: int = 0


class Dispatcher:
    def __init__(
        self,
        store: Store,
        transport: Transport,
        max_attempts: int = 6,
        timeout: float = 10.0,
        base_delay: float = 30.0,
        max_delay: float = 3600.0,
        jitter: float = 0.2,
        clock: Callable[[], float] = time.time,
        rng: Optional[Callable[[], float]] = None,
    ):
        self.store = store
        self.transport = transport
        self.max_attempts = max_attempts
        self.timeout = timeout
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.clock = clock
        self._rng = rng

    def _delay(self, attempt: int, retry_after: Optional[float]) -> float:
        kwargs = {"rng": self._rng} if self._rng else {}
        delay = compute_delay(attempt, self.base_delay, self.max_delay, self.jitter, **kwargs)
        if retry_after is not None:
            # Honour the receiver's hint, but never wait longer than our own cap.
            delay = max(delay, min(retry_after, self.max_delay))
        return delay

    def run_once(self, limit: int = 100) -> DispatchStats:
        stats = DispatchStats()
        now = self.clock()
        for d in self.store.claim_due(now, limit):
            stats.attempted += 1
            if not d["endpoint_active"]:
                # Endpoint was disabled after fan-out: park the delivery instead of calling it.
                self.store.record_attempt(d["id"], now, None, "endpoint disabled", "dead")
                stats.dead += 1
                continue
            body = d["body"].encode()
            ts = int(now)
            headers = {
                "Content-Type": "application/json",
                "User-Agent": "webhook-delivery-service/1.0",
                "Webhook-Id": d["event_id"],
                "Webhook-Timestamp": str(ts),
                "Webhook-Signature": signing.sign(d["secret"], d["event_id"], ts, body),
            }
            attempt = d["attempts"] + 1
            code, error, retry_after = None, None, None
            try:
                result = self.transport.send(d["url"], headers, body, self.timeout)
                code, retry_after = result.status_code, result.retry_after
            except TransportError as exc:
                error = str(exc)
            if code is not None and 200 <= code < 300:
                self.store.record_attempt(d["id"], now, code, None, "succeeded")
                stats.succeeded += 1
                continue
            if code is not None:
                error = f"HTTP {code}"
            permanent = code is not None and 300 <= code < 500 and code not in RETRYABLE_4XX
            if permanent or attempt >= self.max_attempts:
                self.store.record_attempt(d["id"], now, code, error, "dead")
                stats.dead += 1
            else:
                nxt = now + self._delay(attempt, retry_after)
                self.store.record_attempt(d["id"], now, code, error, "pending", nxt)
                stats.retried += 1
        return stats


def build_event_body(event_id: str, event_type: str, data: dict, created_at: float) -> str:
    """Canonical JSON for an event. Rendered once, stored, and re-sent byte-for-byte on retries."""
    return json.dumps(
        {"id": event_id, "type": event_type, "created_at": int(created_at), "data": data},
        separators=(",", ":"),
        sort_keys=True,
    )


def publish_event(
    store: Store,
    event_type: str,
    data: dict,
    now: float,
    idempotency_key: Optional[str] = None,
) -> tuple[dict, bool]:
    event_id = new_id("evt")
    body = build_event_body(event_id, event_type, data, now)
    return store.create_event(event_id, event_type, body, idempotency_key, now)
