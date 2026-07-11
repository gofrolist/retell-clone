"""Retell-compatible webhook signature.

Format (see usan-retirement-backend/supabase/functions/_shared/verify-webhook.ts):

    x-retell-signature: v=<unix_ms>,d=<hex_digest>
    digest = HMAC_SHA256(key=<api_key>, message=<raw_body> + <unix_ms>)  # lowercase hex

The consumer enforces a 5-minute replay window on the timestamp.
"""

import hashlib
import hmac
import time

SIGNATURE_HEADER = "x-retell-signature"


def sign(raw_body: str, api_key: str, timestamp_ms: int | None = None) -> str:
    ts = timestamp_ms if timestamp_ms is not None else int(time.time() * 1000)
    digest = hmac.new(
        api_key.encode(), f"{raw_body}{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return f"v={ts},d={digest}"


def verify(raw_body: str, api_key: str, header: str, max_age_ms: int = 5 * 60 * 1000) -> bool:
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts = int(parts["v"])
        provided = parts["d"]
    except (ValueError, KeyError, AttributeError):
        return False
    if abs(int(time.time() * 1000) - ts) > max_age_ms:
        return False
    expected = hmac.new(
        api_key.encode(), f"{raw_body}{ts}".encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, provided)
