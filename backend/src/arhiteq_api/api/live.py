"""Live Monitoring streams (NOT part of the Retell API contract).

Server-Sent Events behind the dashboard's Live Monitoring page: one stream for
the in-progress call list, one per call for a transcript that fills in as the
conversation happens. Additive — the Retell-compatible REST surface is
unchanged, and the page still works (just at poll cadence) if a proxy eats the
stream.

Why the server polls instead of pushing: transcript updates arrive on whichever
API replica the worker's `/internal/calls/{id}/events` call lands on, which is
rarely the replica holding a given browser's stream. Fanning those out means
Redis pub/sub and its failure modes; a per-stream indexed query every second is
cheaper than that at any live-call volume this platform sees, and it can't miss
an update or drift out of sync with the database.

Auth and ownership are resolved in dependencies, not in the generator body: a
generator only runs once the response has started, so an HTTPException raised
inside it would arrive as a broken 200 stream instead of a 401/404. Each tick
then takes its own short-lived session — a `Depends(get_session)` would pin a
pool connection for the entire (up to half-hour) stream.
"""

import asyncio
import json
from collections.abc import AsyncIterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import session_factory
from ..models import Call, WebhookDelivery
from ..schemas_extra import build_detail_logs, serialize_call
from . import concurrency

router = APIRouter(tags=["live-monitoring"])

# How often a stream re-reads the database. Fast enough that a transcript turn
# lands within a tick of the worker posting it (the worker pumps every 2s), and
# still only one indexed query per second per open dashboard tab.
POLL_INTERVAL_S = 1.0

# Sweeping calls no worker will ever finalize is a write; it doesn't need to
# run on every tick.
SWEEP_EVERY_TICKS = 30

# Streams are bounded so a forgotten tab can't hold a connection open forever
# (and so the GCLB backend timeout is never what cuts them). The dashboard
# reconnects transparently and a reconnect re-sends current state, so a
# rollover loses nothing.
MAX_STREAM_S = 30 * 60

# The list snapshot mirrors what Live Monitoring's table renders. It never
# needs transcripts, and shipping them every second for every live call would
# dwarf the rest of the payload.
_HEAVY_FIELDS = ("transcript", "transcript_object", "transcript_with_tool_calls")


async def stream_workspace(authorization: Annotated[str | None, Header()] = None) -> str:
    """The caller's workspace, with the auth session released immediately."""
    async with session_factory()() as session:
        api_key = await require_api_key(authorization=authorization, session=session)
        return api_key.workspace_id


async def owned_call_workspace(
    call_id: str, workspace_id: Annotated[str, Depends(stream_workspace)]
) -> str:
    async with session_factory()() as session:
        call = await session.get(Call, call_id)
        if call is None or call.workspace_id != workspace_id:
            raise HTTPException(404, detail="Call not found")
    return workspace_id


async def _live_rows(session: AsyncSession, workspace_id: str) -> list[dict[str, Any]]:
    rows = (
        await session.scalars(
            select(Call)
            .where(
                Call.workspace_id == workspace_id,
                Call.call_status.in_(concurrency.LIVE_STATUSES),
            )
            .order_by(Call.created_at_ms.desc())
            .limit(100)
        )
    ).all()
    out = []
    for call in rows:
        row = serialize_call(call)
        for field in _HEAVY_FIELDS:
            row.pop(field, None)
        out.append(row)
    return out


async def _call_snapshot(
    session: AsyncSession, call_id: str, workspace_id: str
) -> dict[str, Any] | None:
    call = await session.get(Call, call_id)
    if call is None or call.workspace_id != workspace_id:
        return None
    out = serialize_call(call)
    deliveries = (
        (
            await session.execute(
                select(WebhookDelivery)
                .where(WebhookDelivery.call_id == call_id)
                .order_by(WebhookDelivery.created_at_ms)
            )
        )
        .scalars()
        .all()
    )
    out["detail_logs"] = build_detail_logs(call, list(deliveries))
    return out


def _ticks() -> range:
    return range(max(1, int(MAX_STREAM_S / POLL_INTERVAL_S)))


@router.get("/live-calls/stream", response_class=EventSourceResponse)
async def stream_live_calls(
    workspace_id: Annotated[str, Depends(stream_workspace)],
) -> AsyncIterable[ServerSentEvent]:
    """`snapshot` events carrying every dialing/ongoing call plus concurrency.

    Sent on connect and thereafter only when something actually changed, so an
    idle workspace costs nothing but FastAPI's keepalive pings.
    """
    previous: str | None = None
    for tick in _ticks():
        async with session_factory()() as session:
            if tick % SWEEP_EVERY_TICKS == 0:
                await concurrency.expire_stale_calls(session, workspace_id)
            payload = {
                "calls": await _live_rows(session, workspace_id),
                "concurrency": {
                    "current_concurrency": await concurrency.count_live_calls(
                        session, workspace_id
                    ),
                    **await concurrency.workspace_concurrency(session, workspace_id),
                },
            }
        serialized = json.dumps(payload, sort_keys=True, default=str)
        if serialized != previous:
            previous = serialized
            yield ServerSentEvent(event="snapshot", data=payload)
        await asyncio.sleep(POLL_INTERVAL_S)


@router.get("/live-calls/{call_id}/stream", response_class=EventSourceResponse)
async def stream_call(
    call_id: str,
    workspace_id: Annotated[str, Depends(owned_call_workspace)],
) -> AsyncIterable[ServerSentEvent]:
    """`call` events for one call, in the get-call shape, until it ends.

    The first event is always the current state, so the drawer can open
    straight onto the stream. Once the call reaches a terminal status the
    stream sends that final state and closes with `end`; post-call analysis
    usually lands later, which is the client's cue to refetch.

    `end` means the call is over — never merely that this stream hit its
    lifetime cap. The client treats it as terminal and stops reconnecting, so
    sending it on the cap would freeze the transcript of a call still running.
    """
    previous: str | None = None
    for _ in _ticks():
        async with session_factory()() as session:
            payload = await _call_snapshot(session, call_id, workspace_id)
        if payload is None:
            # Deleted mid-stream: terminal for a watcher, so say so rather than
            # leaving the client to reconnect into a 404 forever.
            yield ServerSentEvent(event="end", data={"call_id": call_id})
            return
        serialized = json.dumps(payload, sort_keys=True, default=str)
        if serialized != previous:
            previous = serialized
            yield ServerSentEvent(event="call", data=payload)
        if payload.get("call_status") not in concurrency.LIVE_STATUSES:
            yield ServerSentEvent(event="end", data={"call_id": call_id})
            return
        await asyncio.sleep(POLL_INTERVAL_S)
