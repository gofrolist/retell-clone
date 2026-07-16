from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, Call

router = APIRouter(tags=["concurrency"])

# No billing/purchase system: fixed limits, Retell-shaped.
BASE_CONCURRENCY = 20
PURCHASED_CONCURRENCY = 0
CONCURRENCY_PURCHASE_LIMIT = 100
CONCURRENCY_LIMIT = BASE_CONCURRENCY + PURCHASED_CONCURRENCY

# A call occupies a concurrency slot while dialing and while live — the same
# view consumers hold (ACTIVE_CALL = ["registered", "ongoing"]).
LIVE_STATUSES = ("registered", "ongoing")


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
