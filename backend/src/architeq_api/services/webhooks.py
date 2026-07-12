"""Outbound event webhooks (Surface 2B).

Every event POSTs the Retell-shaped `{event, call}` body, signed with
`x-retell-signature` where the HMAC key is the workspace API key — exactly
what the consumer's `verify-webhook.ts` expects.
"""

import asyncio
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import security, signature
from ..config import get_settings
from ..models import Agent, ApiKey, Call, WebhookDelivery, Workspace, now_ms
from ..schemas import call_to_dict
from .metrics import WEBHOOK_DELIVERIES

log = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = [10, 60, 300]


async def resolve_webhook_url(session: AsyncSession, call: Call) -> str | None:
    """Agent-level webhook URL wins over workspace-level (Retell semantics)."""
    agent = await session.get(Agent, call.agent_id)
    if agent is not None and agent.webhook_url:
        return agent.webhook_url
    ws = await session.get(Workspace, call.workspace_id)
    return ws.webhook_url if ws is not None else None


async def signing_key(session: AsyncSession, workspace_id: str) -> str | None:
    key = await session.scalar(
        select(ApiKey.key_material)
        .where(ApiKey.workspace_id == workspace_id, ApiKey.revoked.is_(False))
        .order_by(ApiKey.id)
        .limit(1)
    )
    return key


def build_event_body(event: str, call: Call) -> str:
    # Compact separators to keep the raw body byte-stable for signing.
    return json.dumps({"event": event, "call": call_to_dict(call)}, separators=(",", ":"))


async def send_event(session: AsyncSession, call: Call, event: str) -> None:
    """Deliver one event, with in-process retries. Persists a delivery row."""
    url = await resolve_webhook_url(session, call)
    if not url:
        return
    try:
        security.assert_url_safe(url)
    except security.UnsafeUrlError as exc:
        log.error("refusing webhook to unsafe URL for call %s: %s", call.call_id, exc)
        WEBHOOK_DELIVERIES.labels(event=event, outcome="blocked_unsafe_url").inc()
        return
    key = await signing_key(session, call.workspace_id)
    if key is None:
        log.warning("no active api key to sign webhook for workspace %s", call.workspace_id)
        return
    raw_body = build_event_body(event, call)

    delivery = WebhookDelivery(call_id=call.call_id, event=event, url=url)
    session.add(delivery)
    await session.commit()

    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        for attempt in range(settings.webhook_max_attempts):
            delivery.attempts = attempt + 1
            try:
                resp = await client.post(
                    url,
                    content=raw_body,
                    headers={
                        "content-type": "application/json",
                        signature.SIGNATURE_HEADER: signature.sign(raw_body, key),
                    },
                )
                delivery.last_status_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.delivered = True
                    WEBHOOK_DELIVERIES.labels(event=event, outcome="delivered").inc()
                    break
                delivery.last_error = f"http {resp.status_code}"
            except httpx.HTTPError as exc:
                delivery.last_error = str(exc)
            WEBHOOK_DELIVERIES.labels(event=event, outcome="retry").inc()
            if attempt < settings.webhook_max_attempts - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt, 2)])
                # Re-sign each attempt: the consumer enforces a 5-minute
                # timestamp window and backoff could otherwise stale it.
        else:
            WEBHOOK_DELIVERIES.labels(event=event, outcome="failed").inc()
            delivery.next_attempt_at_ms = now_ms() + 600_000
            log.error("webhook %s for %s failed after retries", event, call.call_id)
    await session.commit()


# Strong references so pending tasks can't be garbage-collected mid-flight;
# also lets tests drain them between cases.
background_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Any) -> None:
    task = asyncio.create_task(coro)
    background_tasks.add(task)

    def _log_failure(t: asyncio.Task[Any]) -> None:
        background_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            log.error("webhook task error", exc_info=t.exception())

    task.add_done_callback(_log_failure)
