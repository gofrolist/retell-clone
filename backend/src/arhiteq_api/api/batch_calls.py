import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session, session_factory
from ..ids import new_batch_call_id, new_call_id
from ..models import Agent, ApiKey, BatchCall, Call, PhoneNumber, now_ms
from ..schemas_extra import CreateBatchCallRequest
from ..services import telephony
from . import concurrency

log = logging.getLogger(__name__)
router = APIRouter(tags=["batch-calls"])

# How often the background drainer re-checks for freed concurrency slots, and
# how long it keeps trying before writing the leftovers off.
BATCH_DRAIN_POLL_S = 2.0
BATCH_DRAIN_DEADLINE_MS = 24 * 60 * 60 * 1000

# Keep strong references so in-flight drainers aren't garbage-collected.
_drain_tasks: set[asyncio.Task] = set()


async def _batch_budget(session: AsyncSession, workspace_id: str, batch_id: str) -> int:
    """Outbound slots this batch may occupy: the workspace's effective outbound
    limit minus the batch's reserved_concurrency (slots held back for
    non-batch calls, per Retell semantics)."""
    limit = await concurrency.effective_concurrency_limit(session, workspace_id)
    batch = await session.get(BatchCall, batch_id)
    reserved = (batch.reserved_concurrency if batch else 0) or 0
    return max(limit - reserved, 0)


async def _dial_wave(
    session: AsyncSession, workspace_id: str, batch_id: str, pending: list[str]
) -> None:
    """Dial as many of `pending` (call ids, mutated in place) as the outbound
    budget allows right now. Pending rows already hold a live status, so they
    are subtracted back out of the live count."""
    budget = await _batch_budget(session, workspace_id, batch_id)
    live = await concurrency.count_live_calls(session, workspace_id, outbound_only=True)
    in_flight = max(live - len(pending), 0)
    slots = max(budget - in_flight, 0)
    wave = pending[:slots]
    del pending[:slots]
    for call_id in wave:
        call = await session.get(Call, call_id)
        if call is None:  # workspace/batch deleted mid-drain
            continue
        try:
            await telephony.start_outbound_call(call)
        except Exception:  # noqa: BLE001 — one bad dial must not sink the batch
            log.exception("batch %s: failed to dial %s", batch_id, call.call_id)
            call.call_status = "error"
            call.disconnection_reason = "error_telephony"
    await session.commit()


async def _drain_batch(workspace_id: str, batch_id: str, pending: list[str]) -> None:
    """Background pacer for tasks that didn't fit the first dial wave."""
    deadline = now_ms() + BATCH_DRAIN_DEADLINE_MS
    factory = session_factory()
    while pending and now_ms() < deadline:
        await asyncio.sleep(BATCH_DRAIN_POLL_S)
        try:
            async with factory() as session:
                await _dial_wave(session, workspace_id, batch_id, pending)
        except Exception:  # noqa: BLE001 — keep pacing through transient DB errors
            log.exception("batch %s: drain wave failed", batch_id)
    if pending:
        log.error("batch %s: gave up on %d undialed tasks", batch_id, len(pending))
        async with factory() as session:
            for call_id in pending:
                call = await session.get(Call, call_id)
                if call is not None and call.call_status == "registered":
                    call.call_status = "not_connected"
                    call.disconnection_reason = "dial_no_answer"
            await session.commit()


@router.post("/create-batch-call", status_code=201)
async def create_batch_call(
    body: CreateBatchCallRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    if not body.tasks:
        raise HTTPException(422, detail="tasks must not be empty")

    number = await session.get(PhoneNumber, body.from_number)
    if number is None or number.workspace_id != api_key.workspace_id:
        raise HTTPException(422, detail=f"from_number {body.from_number} not found in workspace")
    if not number.outbound_agent_id:
        raise HTTPException(422, detail="No outbound agent bound to from_number")
    agent = await session.get(Agent, number.outbound_agent_id)
    if agent is None:
        raise HTTPException(422, detail=f"agent {number.outbound_agent_id} not found")

    scheduled = body.trigger_timestamp is not None
    batch = BatchCall(
        batch_call_id=new_batch_call_id(),
        workspace_id=api_key.workspace_id,
        from_number=body.from_number,
        name=body.name,
        tasks=[t.model_dump(exclude_none=True) for t in body.tasks],
        trigger_timestamp=body.trigger_timestamp,
        reserved_concurrency=body.reserved_concurrency,
        call_time_window=body.call_time_window,
        status="scheduled" if scheduled else "sent",
    )
    session.add(batch)

    calls: list[Call] = []
    for task in body.tasks:
        dyn = {str(k): str(v) for k, v in (task.retell_llm_dynamic_variables or {}).items()}
        call = Call(
            call_id=new_call_id(),
            workspace_id=api_key.workspace_id,
            agent_id=agent.agent_id,
            agent_version=agent.version,
            agent_name=agent.agent_name,
            direction="outbound",
            call_status="registered",
            from_number=body.from_number,
            to_number=task.to_number,
            retell_llm_dynamic_variables=dyn,
            batch_call_id=batch.batch_call_id,
        )
        call.livekit_room = telephony.room_name(call)
        session.add(call)
        calls.append(call)
    await session.commit()

    def batch_response() -> dict:
        # Retell BatchCallResponse shape (additive extras allowed).
        return {
            "batch_call_id": batch.batch_call_id,
            "name": batch.name,
            "from_number": batch.from_number,
            "scheduled_timestamp": batch.trigger_timestamp,
            "total_task_count": len(batch.tasks or []),
            "call_time_window": batch.call_time_window,
            "reserved_concurrency": batch.reserved_concurrency,
        }

    if scheduled:
        # TODO: a real scheduler should dial these at trigger_timestamp; for now
        # scheduled batches are stored only and never dialed automatically.
        return batch_response()

    # Dial what fits the workspace's outbound budget now; the rest is paced by
    # a background drainer as live calls end, so a large batch can't blow past
    # the concurrency limit the per-call endpoints enforce.
    pending = [c.call_id for c in calls]
    await _dial_wave(session, api_key.workspace_id, batch.batch_call_id, pending)
    if pending:
        task = asyncio.create_task(_drain_batch(api_key.workspace_id, batch.batch_call_id, pending))
        _drain_tasks.add(task)
        task.add_done_callback(_drain_tasks.discard)
    return batch_response()
