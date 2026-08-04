from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, Call, Workspace, now_ms, workspace_settings

router = APIRouter(tags=["concurrency"])

# No billing system: purchases are the workspace's `purchased_concurrency`
# setting (Settings → Limits), free up to CONCURRENCY_PURCHASE_LIMIT.
BASE_CONCURRENCY = 20
CONCURRENCY_PURCHASE_LIMIT = 100


def _burst_limit(normal_limit: int) -> int:
    # Retell semantics: burst raises the ceiling to min(3x, +300).
    return min(3 * normal_limit, normal_limit + 300)


async def workspace_concurrency(session: AsyncSession, workspace_id: str) -> dict[str, Any]:
    """Concurrency numbers for a workspace, Retell get-concurrency shaped
    (minus current usage, which callers count separately)."""
    ws = await session.get(Workspace, workspace_id)
    settings = workspace_settings(ws) if ws else {}
    purchased = int(settings.get("purchased_concurrency") or 0)
    limit = BASE_CONCURRENCY + purchased
    burst_enabled = bool(settings.get("concurrency_burst_enabled"))
    return {
        "concurrency_limit": limit,
        "base_concurrency": BASE_CONCURRENCY,
        "purchased_concurrency": purchased,
        "concurrency_purchase_limit": CONCURRENCY_PURCHASE_LIMIT,
        "remaining_purchase_limit": CONCURRENCY_PURCHASE_LIMIT - purchased,
        "reserved_inbound_concurrency": int(settings.get("reserved_inbound_concurrency") or 0),
        "concurrency_burst_enabled": burst_enabled,
        "concurrency_burst_limit": _burst_limit(limit) if burst_enabled else 0,
    }


async def effective_concurrency_limit(
    session: AsyncSession, workspace_id: str, direction: str = "outbound"
) -> int:
    """The ceiling a new call of `direction` must stay under.

    Outbound (and web) calls can't consume slots reserved for inbound
    capacity; inbound calls use the full (possibly burst) limit.
    """
    numbers = await workspace_concurrency(session, workspace_id)
    limit = (
        numbers["concurrency_burst_limit"]
        if numbers["concurrency_burst_enabled"]
        else numbers["concurrency_limit"]
    )
    if direction != "inbound":
        limit -= numbers["reserved_inbound_concurrency"]
    return max(limit, 0)


# A call occupies a concurrency slot while dialing and while live — the same
# view consumers hold (ACTIVE_CALL = ["registered", "ongoing"]).
LIVE_STATUSES = ("registered", "ongoing")


# Calls only leave a live status when a worker finalizes them. Anything the
# worker never picked up (LiveKit dispatch lost, worker down/crashlooping) or
# dropped mid-call (OOM-kill, node preemption) would otherwise sit "dialing" or
# "ongoing" forever: eating the workspace's concurrency budget and showing up
# on Live Monitoring as a call that will never end. These TTLs sweep them to a
# terminal status.
#
# A registered call is still dialing, which no telephony provider stretches
# past a couple of minutes; the generous ceiling only has to beat "forever".
# An ongoing one is bounded by the agent's max_call_duration_ms — which
# defaults to an hour and is not capped, so the ceiling here sits far above any
# call length anyone would configure.
REGISTERED_TTL_MS = 15 * 60 * 1000
ONGOING_TTL_MS = 12 * 60 * 60 * 1000

# Marks a call this sweep gave up on, as opposed to one a worker ended. It is
# how `finalize` recognises a row it may still overwrite: a worker that was
# alive after all must not lose its transcript to a premature sweep.
SWEPT_REASON = "error_worker_lost"


async def expire_stale_calls(session: AsyncSession, workspace_id: str) -> None:
    now = now_ms()
    await session.execute(
        update(Call)
        .where(
            Call.workspace_id == workspace_id,
            Call.call_status == "registered",
            Call.created_at_ms < now - REGISTERED_TTL_MS,
            # Batch tasks are all created "registered" up front and dialed over
            # as long as a day as slots free up (batch_calls._drain_batch), so
            # age says nothing about whether one was ever dialed. Their
            # lifecycle — including giving up on undialed tasks — belongs to
            # the drainer, and sweeping them here would mark a queued task
            # not_connected and then dial it anyway.
            Call.batch_call_id.is_(None),
        )
        .values(
            call_status="not_connected",
            disconnection_reason="dial_no_answer",
            end_timestamp=now,
            duration_ms=0,
        )
    )
    # An abandoned ongoing call did connect, so it ends as an error rather than
    # a failed dial; duration is measured from the answer like a normal call.
    await session.execute(
        update(Call)
        .where(
            Call.workspace_id == workspace_id,
            Call.call_status == "ongoing",
            Call.start_timestamp < now - ONGOING_TTL_MS,
        )
        .values(
            call_status="error",
            disconnection_reason=SWEPT_REASON,
            end_timestamp=now,
            duration_ms=now - Call.start_timestamp,
        )
    )
    await session.commit()


async def count_live_calls(
    session: AsyncSession, workspace_id: str, *, outbound_only: bool = False
) -> int:
    """Live calls in the workspace; with `outbound_only`, just the ones that
    consume the non-reserved (outbound) budget.

    Web calls are stored with direction="inbound" (the column is non-null and
    the field isn't exposed for them) but are gated like outbound calls, so
    the budget filter keys on call_type as well.
    """
    query = (
        select(func.count())
        .select_from(Call)
        .where(Call.workspace_id == workspace_id, Call.call_status.in_(LIVE_STATUSES))
    )
    if outbound_only:
        query = query.where(or_(Call.direction != "inbound", Call.call_type == "web_call"))
    return await session.scalar(query) or 0


async def assert_outbound_capacity(session: AsyncSession, workspace_id: str) -> None:
    """Admit one new outbound (or web) call, or raise 429.

    Retell signals a full channel pool with 429; consumers match
    /concurrency limit|429/i and re-queue instead of marking the lead failed.
    Only calls drawing on the outbound budget count against it: live inbound
    traffic above its reservation must not starve outbound dialing.
    """
    await expire_stale_calls(session, workspace_id)
    limit = await effective_concurrency_limit(session, workspace_id)
    live = await count_live_calls(session, workspace_id, outbound_only=True)
    if live >= limit:
        raise HTTPException(429, detail=f"Concurrency limit reached ({limit})")


@router.get("/get-concurrency")
async def get_concurrency(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    await expire_stale_calls(session, api_key.workspace_id)
    current = await count_live_calls(session, api_key.workspace_id)
    numbers = await workspace_concurrency(session, api_key.workspace_id)
    return {"current_concurrency": current, **numbers}
