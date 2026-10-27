"""HMAC-SHA256 request signing (Standard-Webhooks style) and verification."""
import hashlib
import hmac

DEFAULT_TOLERANCE_SECONDS = 300


def _signed_content(msg_id: str, timestamp: int, body: bytes) -> bytes:
    return f"{msg_id}.{timestamp}.".encode() + body


def sign(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    digest = hmac.new(secret.encode(), _signed_content(msg_id, timestamp, body), hashlib.sha256)
    return f"v1={digest.hexdigest()}"


def verify(
    secret: str,
    msg_id: str,
    timestamp: int,
    body: bytes,
    signature_header: str,
    now: float,
    tolerance: int = DEFAULT_TOLERANCE_SECONDS,
) -> bool:
    """Check a signature header (one or more space-separated `v1=<hex>` values).

    Rejects stale or future timestamps to limit replay, and compares in constant time.
    """
    if abs(now - timestamp) > tolerance:
        return False
    expected = sign(secret, msg_id, timestamp, body)
    return any(hmac.compare_digest(expected, candidate) for candidate in signature_header.split())
