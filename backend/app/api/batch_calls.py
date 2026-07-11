import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..ids import new_batch_call_id, new_call_id
from ..models import Agent, ApiKey, BatchCall, Call, PhoneNumber
from ..schemas_extra import CreateBatchCallRequest
from ..services import telephony

log = logging.getLogger(__name__)
router = APIRouter(tags=["batch-calls"])


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

    if scheduled:
        # TODO: a real scheduler should dial these at trigger_timestamp; for now
        # scheduled batches are stored only and never dialed automatically.
        return {"batch_call_id": batch.batch_call_id}

    for call in calls:
        try:
            await telephony.start_outbound_call(call)
        except Exception:  # noqa: BLE001 — one bad dial must not sink the batch
            log.exception("batch %s: failed to dial %s", batch.batch_call_id, call.call_id)
            call.call_status = "error"
            call.disconnection_reason = "error_telephony"
    await session.commit()
    return {"batch_call_id": batch.batch_call_id}
