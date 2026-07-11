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


@router.get("/get-concurrency")
async def get_concurrency(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    current = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(Call.workspace_id == api_key.workspace_id, Call.call_status == "ongoing")
    )
    return {
        "current_concurrency": current or 0,
        "concurrency_limit": BASE_CONCURRENCY + PURCHASED_CONCURRENCY,
        "base_concurrency": BASE_CONCURRENCY,
        "purchased_concurrency": PURCHASED_CONCURRENCY,
        "concurrency_purchase_limit": CONCURRENCY_PURCHASE_LIMIT,
        "remaining_purchase_limit": CONCURRENCY_PURCHASE_LIMIT - PURCHASED_CONCURRENCY,
    }
