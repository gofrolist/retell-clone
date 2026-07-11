"""Surface 2A — inbound call routing webhook client.

POST {event:"call_inbound", call_inbound:{from_number,to_number}} to the
number's inbound webhook and apply the response. Any failure degrades to the
DID's default inbound agent — an inbound call must always connect.
"""

import logging
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import httpx

from .. import security
from ..config import get_settings
from ..models import PhoneNumber
from .metrics import INBOUND_RESOLUTIONS

log = logging.getLogger(__name__)


def _with_caller_secret(url: str, secret: str) -> str:
    parts = urlparse(url)
    query = parts.query + ("&" if parts.query else "") + urlencode({"caller_secret": secret})
    return urlunparse(parts._replace(query=query))


async def resolve_inbound(
    number: PhoneNumber,
    from_number: str,
    to_number: str,
    function_secret: str | None = None,
) -> tuple[str | None, dict[str, str]]:
    """Returns (override_agent_id, dynamic_variables); falls back to (None, {})."""
    if not number.inbound_webhook_url:
        INBOUND_RESOLUTIONS.labels(outcome="no_webhook").inc()
        return None, {}

    url = number.inbound_webhook_url
    if number.inbound_webhook_secret_in_query and function_secret:
        url = _with_caller_secret(url, function_secret)

    payload = {
        "event": "call_inbound",
        "call_inbound": {"from_number": from_number, "to_number": to_number},
    }
    try:
        security.assert_url_safe(url)
        async with httpx.AsyncClient(
            timeout=get_settings().inbound_webhook_timeout_seconds
        ) as client:
            resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body: dict[str, Any] = resp.json()
        call_inbound = body.get("call_inbound")
        if not isinstance(call_inbound, dict):
            raise ValueError("response missing call_inbound object")
        agent_id = call_inbound.get("override_agent_id")
        dyn = call_inbound.get("dynamic_variables") or {}
        if not isinstance(dyn, dict):
            dyn = {}
        INBOUND_RESOLUTIONS.labels(outcome="webhook_ok").inc()
        return agent_id, {str(k): str(v) for k, v in dyn.items()}
    except Exception as exc:  # noqa: BLE001 — any failure degrades, never drops
        log.warning("inbound webhook failed for %s: %s — using default agent", to_number, exc)
        INBOUND_RESOLUTIONS.labels(outcome="webhook_failed_fallback").inc()
        return None, {}
