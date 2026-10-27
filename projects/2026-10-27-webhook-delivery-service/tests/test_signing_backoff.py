import pytest

from webhooks import signing
from webhooks.backoff import compute_delay

BODY = b'{"a":1}'


def test_sign_is_deterministic_and_known_vector():
    # Independently computed: HMAC-SHA256("s3cret", 'msg_1.1700000000.{"a":1}')
    import hashlib
    import hmac

    expected = hmac.new(b"s3cret", b'msg_1.1700000000.{"a":1}', hashlib.sha256).hexdigest()
    assert signing.sign("s3cret", "msg_1", 1_700_000_000, BODY) == f"v1={expected}"


def test_verify_accepts_valid_and_multiple_signatures():
    sig = signing.sign("k", "m", 100, BODY)
    assert signing.verify("k", "m", 100, BODY, sig, now=100)
    assert signing.verify("k", "m", 100, BODY, f"v1=deadbeef {sig}", now=100)


@pytest.mark.parametrize(
    "mutate",
    [
        dict(secret="other"),
        dict(msg_id="m2"),
        dict(body=b'{"a":2}'),
        dict(timestamp=101),
    ],
)
def test_verify_rejects_tampering(mutate):
    sig = signing.sign("k", "m", 100, BODY)
    args = dict(secret="k", msg_id="m", timestamp=100, body=BODY)
    args.update(mutate)
    assert not signing.verify(**args, signature_header=sig, now=100)


def test_verify_rejects_stale_and_future_timestamps():
    sig = signing.sign("k", "m", 1000, BODY)
    assert signing.verify("k", "m", 1000, BODY, sig, now=1000 + 300)
    assert not signing.verify("k", "m", 1000, BODY, sig, now=1000 + 301)
    assert not signing.verify("k", "m", 1000, BODY, sig, now=1000 - 301)


def test_backoff_doubles_then_caps():
    delays = [compute_delay(n, base=30, cap=200, jitter=0) for n in range(1, 6)]
    assert delays == [30, 60, 120, 200, 200]


def test_backoff_jitter_only_shortens_within_bounds():
    assert compute_delay(1, base=100, jitter=0.2, rng=lambda: 1.0) == pytest.approx(80)
    assert compute_delay(1, base=100, jitter=0.2, rng=lambda: 0.0) == pytest.approx(100)


def test_backoff_rejects_bad_attempt():
    with pytest.raises(ValueError):
        compute_delay(0)
