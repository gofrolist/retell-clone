"""Live Monitoring: the SSE streams behind the dashboard page, and the sweep
that keeps calls no worker ever finalized from sitting there forever.

httpx's ASGI transport buffers a response until the app finishes it, so these
tests shrink MAX_STREAM_S instead of reading a stream incrementally: the
request returns the whole SSE body a few ticks later. Events that have to land
*during* a stream are posted from a concurrent task.
"""

import asyncio
import json

import pytest
from sqlalchemy import update

import arhiteq_api.db as db_module
from arhiteq_api.api import concurrency, live
from arhiteq_api.models import Call, now_ms
from tests.conftest import (
    AUTH_HEADERS,
    FROM_NUMBER,
    INTERNAL_HEADERS,
    OTHER_AUTH_HEADERS,
    WORKSPACE_ID,
)

TICK = 0.02


@pytest.fixture(autouse=True)
def _short_streams(monkeypatch):
    monkeypatch.setattr(live, "POLL_INTERVAL_S", TICK)
    monkeypatch.setattr(live, "MAX_STREAM_S", TICK * 10)


def _events(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, payload) pairs, ignoring keepalives."""
    out: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        name = "message"
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data = line.removeprefix("data:").strip()
        if data is not None:
            out.append((name, json.loads(data)))
    return out


async def _place_call(client, to_number: str = "+15550001111") -> str:
    resp = await client.post(
        "/v2/create-phone-call",
        headers=AUTH_HEADERS,
        json={"from_number": FROM_NUMBER, "to_number": to_number},
    )
    assert resp.status_code == 201
    return resp.json()["call_id"]


async def _answer(client, call_id: str, start_timestamp: int | None = None) -> None:
    resp = await client.post(
        f"/internal/calls/{call_id}/events",
        headers=INTERNAL_HEADERS,
        json={"event": "call_started", "start_timestamp": start_timestamp or now_ms()},
    )
    assert resp.status_code == 200


async def test_live_stream_snapshots_dialing_calls(client):
    call_id = await _place_call(client)
    resp = await client.get("/live-calls/stream", headers=AUTH_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    event, payload = _events(resp.text)[0]
    assert event == "snapshot"
    assert [c["call_id"] for c in payload["calls"]] == [call_id]
    assert payload["calls"][0]["call_status"] == "registered"
    assert payload["concurrency"]["current_concurrency"] == 1
    assert payload["concurrency"]["concurrency_limit"] == concurrency.BASE_CONCURRENCY
    # Transcripts belong to the per-call stream; the table never renders them.
    assert "transcript_object" not in payload["calls"][0]


async def test_live_stream_repeats_itself_only_when_something_changed(client):
    call_id = await _place_call(client)
    stream = asyncio.create_task(client.get("/live-calls/stream", headers=AUTH_HEADERS))
    await asyncio.sleep(TICK * 3)
    await _answer(client, call_id)
    events = _events((await stream).text)

    # Many ticks, two states: registered, then ongoing.
    statuses = [e[1]["calls"][0]["call_status"] for e in events]
    assert statuses == ["registered", "ongoing"]


async def test_live_stream_is_workspace_scoped(client, other_workspace):
    await _place_call(client)
    resp = await client.get("/live-calls/stream", headers=OTHER_AUTH_HEADERS)
    assert _events(resp.text)[0][1]["calls"] == []


async def test_live_stream_requires_auth(client):
    resp = await client.get("/live-calls/stream")
    assert resp.status_code == 401


async def test_call_stream_follows_the_transcript_then_ends_with_the_call(client):
    call_id = await _place_call(client)
    await _answer(client, call_id)

    stream = asyncio.create_task(client.get(f"/live-calls/{call_id}/stream", headers=AUTH_HEADERS))
    await asyncio.sleep(TICK * 2)
    await client.post(
        f"/internal/calls/{call_id}/events",
        headers=INTERNAL_HEADERS,
        json={
            "event": "transcript_update",
            "transcript": "Agent: Hi there",
            "transcript_object": [{"role": "agent", "content": "Hi there"}],
            "transcript_with_tool_calls": [
                {"role": "agent", "content": "Hi there"},
                {"role": "tool_call_invocation", "name": "kb_lookup", "arguments": "{}"},
            ],
        },
    )
    await asyncio.sleep(TICK * 2)
    await client.post(
        f"/internal/calls/{call_id}/finalize",
        headers=INTERNAL_HEADERS,
        json={"end_timestamp": now_ms(), "duration_ms": 1000},
    )
    events = _events((await stream).text)

    assert events[0][0] == "call"
    assert events[0][1]["call_status"] == "ongoing"
    assert events[1][1]["transcript_object"] == [{"role": "agent", "content": "Hi there"}]
    # Tool activity streams live too, so the drawer can show it mid-call.
    assert events[1][1]["transcript_with_tool_calls"][1]["name"] == "kb_lookup"
    assert events[-2][1]["call_status"] == "ended"
    # A finalize must not wipe what transcript_update accumulated.
    assert events[-2][1]["transcript"] == "Agent: Hi there"
    assert events[-1] == ("end", {"call_id": call_id})


async def test_call_stream_hides_another_workspaces_call(client, other_workspace):
    call_id = await _place_call(client)
    resp = await client.get(f"/live-calls/{call_id}/stream", headers=OTHER_AUTH_HEADERS)
    assert resp.status_code == 404


async def test_stale_dialing_call_is_swept_off_the_live_view(client):
    call_id = await _place_call(client)
    async with db_module.session_factory()() as session:
        await session.execute(
            update(Call)
            .where(Call.call_id == call_id)
            .values(created_at_ms=now_ms() - concurrency.REGISTERED_TTL_MS - 1)
        )
        await session.commit()

    resp = await client.get("/live-calls/stream", headers=AUTH_HEADERS)
    payload = _events(resp.text)[0][1]
    assert payload["calls"] == []
    assert payload["concurrency"]["current_concurrency"] == 0

    got = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    assert got["call_status"] == "not_connected"
    assert got["disconnection_reason"] == "dial_no_answer"


async def test_abandoned_ongoing_call_is_swept_as_an_error(client):
    call_id = await _place_call(client)
    await _answer(client, call_id, start_timestamp=now_ms() - concurrency.ONGOING_TTL_MS - 1)

    async with db_module.session_factory()() as session:
        await concurrency.expire_stale_calls(session, WORKSPACE_ID)

    got = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    assert got["call_status"] == "error"
    assert got["disconnection_reason"] == "error_unknown"
    assert got["duration_ms"] > 0


async def test_a_live_call_is_never_swept(client):
    call_id = await _place_call(client)
    await _answer(client, call_id)
    async with db_module.session_factory()() as session:
        await concurrency.expire_stale_calls(session, WORKSPACE_ID)

    got = (await client.get(f"/v2/get-call/{call_id}", headers=AUTH_HEADERS)).json()
    assert got["call_status"] == "ongoing"
