"""Per-agent Webhook Settings — event subscription, timeout, and the dashboard
Test button. All additive to Retell's shape; agent-level config wins over the
workspace fallback."""

import asyncio
import json

import respx
from httpx import Response
from sqlalchemy import update

from arhiteq_api import signature
from arhiteq_api.db import session_factory
from arhiteq_api.models import Agent
from tests.conftest import (
    API_KEY,
    AUTH_HEADERS,
    COMPANION_AGENT_ID,
    FROM_NUMBER,
    INTERNAL_HEADERS,
    WORKSPACE_ID,
)

AGENT_WEBHOOK_URL = "https://consumer.example/functions/v1/agent-hook"


async def _set_agent_webhook(agent_id: str, **values) -> None:
    async with session_factory()() as session:
        await session.execute(update(Agent).where(Agent.agent_id == agent_id).values(**values))
        await session.commit()


async def _finalize_a_call(client) -> str:
    created = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": "+18155141544"},
    )
    call_id = created.json()["call_id"]
    await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={
            "duration_ms": 12000,
            "call_status": "ended",
            "disconnection_reason": "user_hangup",
            "transcript": "Agent: Hi.\nUser: Hello.",
        },
    )
    return call_id


@respx.mock
async def test_agent_events_subscription_filters_delivery(client):
    # FROM_NUMBER routes to COMPANION_AGENT_ID; subscribe it to call_ended only.
    await _set_agent_webhook(
        COMPANION_AGENT_ID, webhook_url=AGENT_WEBHOOK_URL, webhook_events=["call_ended"]
    )
    route = respx.post(AGENT_WEBHOOK_URL).mock(return_value=Response(200))

    await _finalize_a_call(client)
    # Give both fire-and-forget events a chance; only the subscribed one lands.
    for _ in range(30):
        await asyncio.sleep(0.05)
        if route.call_count >= 1:
            break
    await asyncio.sleep(0.3)

    events = [json.loads(c.request.content.decode())["event"] for c in route.calls]
    assert events == ["call_ended"], f"expected only call_ended, got {events}"


@respx.mock
async def test_agent_null_events_delivers_default_set(client):
    # webhook_url set, webhook_events left null → the default subscription
    # (call_started/ended/analyzed). finalize fires call_ended + call_analyzed.
    await _set_agent_webhook(COMPANION_AGENT_ID, webhook_url=AGENT_WEBHOOK_URL)
    route = respx.post(AGENT_WEBHOOK_URL).mock(return_value=Response(200))

    await _finalize_a_call(client)
    for _ in range(50):
        await asyncio.sleep(0.05)
        if route.call_count >= 2:
            break

    events = {json.loads(c.request.content.decode())["event"] for c in route.calls}
    assert events == {"call_ended", "call_analyzed"}


@respx.mock
async def test_test_webhook_button_posts_signed_sample(client):
    route = respx.post(AGENT_WEBHOOK_URL).mock(return_value=Response(204))

    resp = await client.post(
        f"/test-agent-webhook/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_url": AGENT_WEBHOOK_URL, "event": "call_analyzed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "status_code": 204, "error": None}

    assert route.call_count == 1
    raw = route.calls[0].request.content.decode()
    header = route.calls[0].request.headers["x-retell-signature"]
    assert signature.verify(raw, API_KEY, header)
    payload = json.loads(raw)
    assert payload["event"] == "call_analyzed"
    assert payload["call"]["metadata"] == {"arhiteq_test": True}
    assert payload["call"]["agent_id"] == COMPANION_AGENT_ID


@respx.mock
async def test_test_webhook_reports_failure_status(client):
    respx.post(AGENT_WEBHOOK_URL).mock(return_value=Response(500))
    resp = await client.post(
        f"/test-agent-webhook/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_url": AGENT_WEBHOOK_URL},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "status_code": 500, "error": "HTTP 500"}


async def test_test_webhook_without_url_is_422(client):
    resp = await client.post(
        f"/test-agent-webhook/{COMPANION_AGENT_ID}", headers=AUTH_HEADERS, json={}
    )
    assert resp.status_code == 422


async def test_update_agent_rejects_unknown_event(client):
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_events": ["call_ended", "bogus_event"]},
    )
    assert resp.status_code == 422
    assert "bogus_event" in resp.json()["detail"]


async def test_update_agent_rejects_out_of_range_timeout(client):
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_timeout_ms": 999999},
    )
    assert resp.status_code == 422


async def test_update_agent_accepts_valid_webhook_overrides(client):
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_timeout_ms": 5000, "webhook_events": ["call_ended", "call_analyzed"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["webhook_timeout_ms"] == 5000
    assert body["webhook_events"] == ["call_ended", "call_analyzed"]


async def test_update_agent_accepts_transfer_events_for_retell_parity(client):
    # The full Retell catalog is subscribable even though the worker doesn't
    # fire the transfer/transcript events yet.
    events = ["transcript_updated", "transfer_started", "transfer_bridged", "transfer_ended"]
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_events": events},
    )
    assert resp.status_code == 200
    assert resp.json()["webhook_events"] == events


async def test_update_agent_dedupes_events_and_coerces_int_timeout(client):
    # PATCH normalizes like the create (Pydantic) path: de-dupe events, accept an
    # integer-valued float timeout.
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={
            "webhook_events": ["call_ended", "call_ended", "call_started"],
            "webhook_timeout_ms": 5000.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["webhook_events"] == ["call_ended", "call_started"]
    assert body["webhook_timeout_ms"] == 5000


async def test_update_agent_rejects_fractional_timeout(client):
    resp = await client.patch(
        f"/update-agent/{COMPANION_AGENT_ID}",
        headers=AUTH_HEADERS,
        json={"webhook_timeout_ms": 5000.5},
    )
    assert resp.status_code == 422


async def test_resolve_target_uses_agent_defaults_when_null(client):
    # Null timeout/events resolve to the agent-level defaults the dashboard shows
    # (5s, the three call_* events) — NOT the platform 10s / "all events".
    from arhiteq_api.models import DEFAULT_WEBHOOK_EVENTS, Call
    from arhiteq_api.services.webhooks import resolve_webhook_target

    await _set_agent_webhook(COMPANION_AGENT_ID, webhook_url=AGENT_WEBHOOK_URL)
    async with session_factory()() as session:
        call = Call(
            call_id="call_resolve_test",
            workspace_id=WORKSPACE_ID,
            agent_id=COMPANION_AGENT_ID,
            direction="outbound",
        )
        session.add(call)
        await session.commit()
        target = await resolve_webhook_target(session, call)

    assert target is not None
    assert target.timeout_seconds == 5.0
    assert target.events == frozenset(DEFAULT_WEBHOOK_EVENTS)
    assert target.wants("call_ended") is True
    assert target.wants("transfer_started") is False
