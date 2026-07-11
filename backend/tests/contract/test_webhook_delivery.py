"""Surface 2B delivery — call_ended/call_analyzed with valid signatures and
the exact payload fields the consumer reads."""

import asyncio
import json

import respx
from httpx import Response
from sqlalchemy import update

from app import signature
from app.db import session_factory
from app.models import Workspace
from tests.conftest import API_KEY, AUTH_HEADERS, FROM_NUMBER, INTERNAL_HEADERS, WORKSPACE_ID

WEBHOOK_URL = "https://consumer.example/functions/v1/retell-call-ended"


async def _set_workspace_webhook():
    async with session_factory()() as session:
        await session.execute(
            update(Workspace).where(Workspace.id == WORKSPACE_ID).values(webhook_url=WEBHOOK_URL)
        )
        await session.commit()


@respx.mock
async def test_call_ended_and_analyzed_delivered_with_valid_signature(client):
    await _set_workspace_webhook()
    route = respx.post(WEBHOOK_URL).mock(return_value=Response(200))

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
            "duration_ms": 134000,
            "call_status": "ended",
            "disconnection_reason": "user_hangup",
            "transcript": "Agent: Hi.\nUser: Hello.",
            "recording_url": "https://storage.example/rec.mp3",
        },
    )
    for _ in range(50):
        if route.call_count >= 2:
            break
        await asyncio.sleep(0.1)
    assert route.call_count >= 2, "expected call_ended and call_analyzed deliveries"

    events = {}
    for request_call in route.calls:
        raw = request_call.request.content.decode()
        header = request_call.request.headers["x-retell-signature"]
        # signature key = the workspace API key (Retell semantics)
        assert signature.verify(raw, API_KEY, header)
        payload = json.loads(raw)
        events[payload["event"]] = payload

    assert set(events) == {"call_ended", "call_analyzed"}
    call = events["call_ended"]["call"]
    # exact field set the consumer's handler reads:
    assert call["call_id"] == call_id
    assert call["direction"] == "outbound"
    assert call["from_number"] == FROM_NUMBER
    assert call["to_number"] == "+18155141544"
    assert call["duration_ms"] == 134000
    assert call["disconnection_reason"] == "user_hangup"
    assert call["recording_url"] == "https://storage.example/rec.mp3"
    assert call["transcript"] == "Agent: Hi.\nUser: Hello."
    # same call_id on both events (consumer dedups by call_id)
    assert events["call_analyzed"]["call"]["call_id"] == call_id
    # call_analyzed carries the analysis with the `summary` spelling
    analyzed = events["call_analyzed"]["call"]["call_analysis"]
    assert "summary" in analyzed and "in_voicemail" in analyzed and "user_sentiment" in analyzed
