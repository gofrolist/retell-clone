"""Outbound event webhooks (Surface 2B).

Every event POSTs the Retell-shaped `{event, call}` body, signed with
`x-retell-signature` where the HMAC key is the workspace API key — exactly
what the consumer's `verify-webhook.ts` expects.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import security, signature
from ..config import get_settings
from ..models import Agent, ApiKey, Call, WebhookDelivery, Workspace, now_ms
from ..schemas import call_to_dict
from .metrics import WEBHOOK_DELIVERIES

log = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = [10, 60, 300]


@dataclass(frozen=True)
class WebhookTarget:
    """Resolved outbound-webhook config for one call."""

    url: str
    timeout_seconds: float
    # None = deliver every event; a set restricts to those event names. Only the
    # agent-level URL can subscribe; the workspace fallback always gets all.
    events: frozenset[str] | None

    def wants(self, event: str) -> bool:
        return self.events is None or event in self.events


async def resolve_webhook_target(session: AsyncSession, call: Call) -> WebhookTarget | None:
    """Agent-level webhook (URL + overrides) wins over the workspace fallback."""
    settings = get_settings()
    agent = await session.get(Agent, call.agent_id)
    if agent is not None and agent.webhook_url:
        timeout = (
            agent.webhook_timeout_ms / 1000
            if agent.webhook_timeout_ms
            else settings.webhook_timeout_seconds
        )
        events = frozenset(agent.webhook_events) if agent.webhook_events is not None else None
        return WebhookTarget(agent.webhook_url, timeout, events)
    ws = await session.get(Workspace, call.workspace_id)
    if ws is not None and ws.webhook_url:
        return WebhookTarget(ws.webhook_url, settings.webhook_timeout_seconds, None)
    return None


async def resolve_webhook_url(session: AsyncSession, call: Call) -> str | None:
    """Agent-level webhook URL wins over workspace-level (Retell semantics)."""
    target = await resolve_webhook_target(session, call)
    return target.url if target is not None else None


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
    target = await resolve_webhook_target(session, call)
    if target is None:
        return
    if not target.wants(event):
        # Agent unsubscribed from this event in its Webhook Settings.
        return
    url = target.url
    try:
        # DNS resolution is blocking; keep it off the event loop.
        await run_in_threadpool(security.assert_url_safe, url)
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
    async with httpx.AsyncClient(timeout=target.timeout_seconds) as client:
        for attempt in range(settings.webhook_max_attempts):
            delivery.attempts = attempt + 1
            try:
                resp = await client.post(
                    url,
                    content=raw_body,
                    headers={
                        "content-type": "application/json",
                        # Re-sign each attempt: the consumer enforces a 5-minute
                        # timestamp window and backoff could otherwise stale it.
                        signature.SIGNATURE_HEADER: signature.sign(raw_body, key),
                    },
                )
                delivery.last_status_code = resp.status_code
                if 200 <= resp.status_code < 300:
                    delivery.delivered = True
                    WEBHOOK_DELIVERIES.labels(event=event, outcome="delivered").inc()
                    await session.commit()
                    return
                delivery.last_error = f"http {resp.status_code}"
            except httpx.HTTPError as exc:
                delivery.last_error = str(exc)
            WEBHOOK_DELIVERIES.labels(event=event, outcome="retry").inc()
            # Commit per attempt so the DB connection is returned to the pool
            # during the sleep (a slow consumer would otherwise pin it for
            # minutes) and intermediate state survives a process crash.
            await session.commit()
            if attempt < settings.webhook_max_attempts - 1:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS[min(attempt, 2)])

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
