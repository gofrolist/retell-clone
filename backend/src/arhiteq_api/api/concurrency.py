from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, Call, Workspace, now_ms, workspace_settings

router = APIRouter(tags=["concurrency"])

# No billing system: purchases are the workspace's `purchased_concurrency`
# setting (Settings → Limits), free up to CONCURRENCY_PURCHASE_LIMIT.
BASE_CONCURRENCY = 20
CONCURRENCY_PURCHASE_LIMIT = 100
# Fallback when a workspace row is missing (shouldn't happen in practice).
CONCURRENCY_LIMIT = BASE_CONCURRENCY


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


# Web calls that never got answered by a worker (down/crashlooping) have no
# finalizer: sweep them to a terminal status before counting, so dead test
# calls can't eat the workspace's concurrency budget.
WEB_CALL_REGISTERED_TTL_MS = 15 * 60 * 1000


async def expire_stale_web_calls(session: AsyncSession, workspace_id: str) -> None:
    cutoff = now_ms() - WEB_CALL_REGISTERED_TTL_MS
    await session.execute(
        update(Call)
        .where(
            Call.workspace_id == workspace_id,
            Call.call_type == "web_call",
            Call.call_status == "registered",
            Call.created_at_ms < cutoff,
        )
        .values(call_status="not_connected", disconnection_reason="dial_no_answer")
    )
    await session.commit()


async def count_live_calls(session: AsyncSession, workspace_id: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(Call)
            .where(Call.workspace_id == workspace_id, Call.call_status.in_(LIVE_STATUSES))
        )
        or 0
    )


@router.get("/get-concurrency")
async def get_concurrency(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    current = await count_live_calls(session, api_key.workspace_id)
    numbers = await workspace_concurrency(session, api_key.workspace_id)
    return {"current_concurrency": current, **numbers}
