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
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import security, signature
from ..config import get_settings
from ..models import (
    DEFAULT_WEBHOOK_EVENTS,
    DEFAULT_WEBHOOK_TIMEOUT_MS,
    Agent,
    ApiKey,
    Call,
    WebhookDelivery,
    Workspace,
    now_ms,
)
from ..schemas import call_to_dict
from .metrics import WEBHOOK_DELIVERIES

log = logging.getLogger(__name__)

RETRY_BACKOFF_SECONDS = [10, 60, 300]


@dataclass(frozen=True)
class WebhookTarget:
    """Resolved outbound-webhook config for one call."""

    url: str
    timeout_seconds: float
    # None = deliver every event (the workspace-level catch-all). A set restricts
    # to those event names; an agent-level target always carries a concrete set
    # (its subscription, or DEFAULT_WEBHOOK_EVENTS when unconfigured).
    events: frozenset[str] | None

    def wants(self, event: str) -> bool:
        return self.events is None or event in self.events


async def resolve_webhook_target(session: AsyncSession, call: Call) -> WebhookTarget | None:
    """Agent-level webhook (URL + overrides) wins over the workspace fallback."""
    settings = get_settings()
    agent = await session.get(Agent, call.agent_id)
    if agent is not None and agent.webhook_url:
        # Null timeout/events fall back to the agent-level defaults the dashboard
        # displays, so what the operator sees is what actually ships.
        timeout = (agent.webhook_timeout_ms or DEFAULT_WEBHOOK_TIMEOUT_MS) / 1000
        events = frozenset(
            agent.webhook_events if agent.webhook_events is not None else DEFAULT_WEBHOOK_EVENTS
        )
        return WebhookTarget(agent.webhook_url, timeout, events)
    ws = await session.get(Workspace, call.workspace_id)
    if ws is not None and ws.webhook_url:
        return WebhookTarget(ws.webhook_url, settings.webhook_timeout_seconds, None)
    return None


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


def sample_call(workspace_id: str, agent: Agent | None = None) -> Call:
    """A representative, non-persisted Call for the dashboard "Test" button.

    Fed through build_event_body so the signed sample stays byte-identical in
    shape to a real delivery. Marked with metadata so consumers can drop it if
    they choose. `agent` is None for the workspace-level test, which has no
    agent to describe.
    """
    ts = now_ms()
    return Call(
        call_id="call_test_webhook",
        workspace_id=workspace_id,
        agent_id=agent.agent_id if agent is not None else "agent_test_webhook",
        agent_version=agent.version if agent is not None else 0,
        agent_name=agent.agent_name if agent is not None else "Test agent",
        call_type="web_call",
        call_status="ended",
        direction="outbound",
        from_number="+15551234567",
        to_number="+15557654321",
        metadata_={"arhiteq_test": True},
        start_timestamp=ts - 30_000,
        end_timestamp=ts,
        duration_ms=30_000,
        disconnection_reason="agent_hangup",
        transcript="Agent: This is a test webhook from Arhiteq.\nUser: Great, it works!",
        call_analysis={
            "call_summary": "Test webhook delivery from the Arhiteq dashboard.",
            "user_sentiment": "Positive",
            "call_successful": True,
            "in_voicemail": False,
        },
    )


async def send_test_event(
    session: AsyncSession,
    workspace_id: str,
    *,
    url: str,
    event: str,
    call: Call,
    timeout_ms: int,
) -> dict[str, Any]:
    """Deliver one signed sample event and report the outcome, no retries.

    The single implementation behind /test-agent-webhook and
    /test-workspace-webhook, so the SSRF gate, the signature header and the
    {ok, status_code, error} result shape can't drift between them.
    """
    try:
        # DNS resolution is blocking; keep it off the event loop (and this is
        # the SSRF gate — the URL is user-supplied).
        await run_in_threadpool(security.assert_url_safe, url)
    except security.UnsafeUrlError as exc:
        raise HTTPException(422, detail=f"Refusing to send to unsafe URL: {exc}") from None

    key = await signing_key(session, workspace_id)
    if key is None:
        raise HTTPException(409, detail="No active API key available to sign the webhook")

    raw_body = build_event_body(event, call)
    try:
        async with httpx.AsyncClient(timeout=timeout_ms / 1000) as client:
            resp = await client.post(
                url,
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    signature.SIGNATURE_HEADER: signature.sign(raw_body, key),
                },
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}
    ok = 200 <= resp.status_code < 300
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "error": None if ok else f"HTTP {resp.status_code}",
    }


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
