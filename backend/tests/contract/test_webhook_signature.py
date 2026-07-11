"""Surface 2B signature — must match consumer's verify-webhook.ts exactly:
header `v={unix_ms},d={hex}`, digest = HMAC_SHA256(key=api_key, rawBody+ts),
lowercase hex, 5-minute replay window."""

import hashlib
import hmac
import re
import time

from app import signature


def test_header_format():
    header = signature.sign('{"event":"call_ended"}', "test-key")
    assert re.fullmatch(r"v=\d+,d=[0-9a-f]+", header)


def test_digest_matches_reference_implementation():
    raw_body = '{"event":"call_ended","call":{"call_id":"call_abc"}}'
    api_key = "key_supersecret"
    ts = 1750000000000
    header = signature.sign(raw_body, api_key, timestamp_ms=ts)
    expected = hmac.new(
        api_key.encode(), f"{raw_body}{ts}".encode(), hashlib.sha256
    ).hexdigest()
    assert header == f"v={ts},d={expected}"


def test_roundtrip_verifies():
    raw = '{"x":1}'
    header = signature.sign(raw, "k")
    assert signature.verify(raw, "k", header)


def test_stale_timestamp_rejected():
    raw = "{}"
    stale = int(time.time() * 1000) - 6 * 60 * 1000
    header = signature.sign(raw, "k", timestamp_ms=stale)
    assert not signature.verify(raw, "k", header)


def test_wrong_key_and_tampered_body_rejected():
    raw = '{"a":1}'
    header = signature.sign(raw, "k")
    assert not signature.verify(raw, "other-key", header)
    assert not signature.verify('{"a":2}', "k", header)


def test_malformed_header_rejected():
    assert not signature.verify("{}", "k", "garbage")
    assert not signature.verify("{}", "k", "")
