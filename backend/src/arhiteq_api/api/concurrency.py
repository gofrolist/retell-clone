from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, Call, now_ms

router = APIRouter(tags=["concurrency"])

# No billing/purchase system: fixed limits, Retell-shaped.
BASE_CONCURRENCY = 20
PURCHASED_CONCURRENCY = 0
CONCURRENCY_PURCHASE_LIMIT = 100
CONCURRENCY_LIMIT = BASE_CONCURRENCY + PURCHASED_CONCURRENCY

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
    return {
        "current_concurrency": current,
        "concurrency_limit": CONCURRENCY_LIMIT,
        "base_concurrency": BASE_CONCURRENCY,
        "purchased_concurrency": PURCHASED_CONCURRENCY,
        "concurrency_purchase_limit": CONCURRENCY_PURCHASE_LIMIT,
        "remaining_purchase_limit": CONCURRENCY_PURCHASE_LIMIT - PURCHASED_CONCURRENCY,
    }
