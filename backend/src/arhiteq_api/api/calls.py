import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..config import get_settings
from ..db import get_session
from ..ids import new_call_id
from ..models import Agent, ApiKey, Call, PhoneNumber, WebhookDelivery
from ..schemas import CreatePhoneCallRequest, ListCallsRequest, call_to_dict
from ..schemas_extra import (
    CreateWebCallRequest,
    RegisterPhoneCallRequest,
    UpdateCallRequest,
    build_detail_logs,
    serialize_call,
    web_call_to_dict,
)
from ..services import analysis, telephony
from . import concurrency

log = logging.getLogger(__name__)
router = APIRouter(tags=["calls"])

E164 = re.compile(r"^\+[1-9]\d{6,14}$")

# Warn once per process when create-web-call has to fall back to the internal
# LiveKit URL because ARHITEQ_PUBLIC_LIVEKIT_URL is unset.
_warned_public_livekit_fallback = False


def _coerce_dynamic_variables(raw: dict | None) -> dict[str, str]:
    # Stored verbatim: arbitrary string keys, values coerced to strings
    # (contract: don't rename, don't drop).
    return {str(k): str(v) for k, v in (raw or {}).items()}


async def _get_workspace_agent(session: AsyncSession, workspace_id: str, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise HTTPException(422, detail=f"agent {agent_id} not found")
    return agent


def _web_call_access_token(call: Call) -> str:
    """LiveKit room-join token for a web call."""
    settings = get_settings()
    try:
        from livekit import api as lk_api

        return (
            lk_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
            .with_identity(f"web_{call.call_id}")
            .with_grants(lk_api.VideoGrants(room_join=True, room=call.livekit_room or ""))
            .to_jwt()
        )
    except Exception as exc:  # noqa: BLE001
        # A fake token would 201 a web call that can never join the room, with
        # the failure surfacing later, disconnected from the cause. Fail now.
        log.exception("failed to mint LiveKit access token for %s", call.call_id)
        raise HTTPException(500, detail="Failed to mint web call access token") from exc


@router.post("/v2/create-phone-call", status_code=201)
async def create_phone_call(
    body: CreatePhoneCallRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    if not body.ignore_e164_validation and not E164.match(body.from_number):
        raise HTTPException(422, detail="from_number must be in E.164 format")
    if not E164.match(body.to_number):
        raise HTTPException(422, detail="to_number must be in E.164 format")

    number = await session.get(PhoneNumber, body.from_number)
    if number is None or number.workspace_id != api_key.workspace_id:
        raise HTTPException(422, detail=f"from_number {body.from_number} not found in workspace")

    agent_id = body.override_agent_id or number.outbound_agent_id
    if not agent_id:
        raise HTTPException(422, detail="No agent bound to from_number and no override_agent_id")
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(422, detail=f"agent {agent_id} not found")

    # Retell signals a full channel pool with 429; consumers match
    # /concurrency limit|429/i and re-queue instead of marking the lead failed.
    await concurrency.expire_stale_web_calls(session, api_key.workspace_id)
    if await concurrency.count_live_calls(session, api_key.workspace_id) >= (
        concurrency.CONCURRENCY_LIMIT
    ):
        raise HTTPException(
            429, detail=f"Concurrency limit reached ({concurrency.CONCURRENCY_LIMIT})"
        )

    # Dynamic variables are stored verbatim: arbitrary string keys, values
    # coerced to strings (contract: don't rename, don't drop).
    dyn = {str(k): str(v) for k, v in (body.retell_llm_dynamic_variables or {}).items()}

    call = Call(
        workspace_id=api_key.workspace_id,
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_name=agent.agent_name,
        direction="outbound",
        call_status="registered",
        from_number=body.from_number,
        to_number=body.to_number,
        metadata_=body.metadata,
        retell_llm_dynamic_variables=dyn,
        custom_sip_headers=body.custom_sip_headers,
    )
    call.livekit_room = telephony.room_name(call)
    session.add(call)
    await session.commit()

    try:
        await telephony.start_outbound_call(call)
    except Exception:
        log.exception("failed to start outbound call %s", call.call_id)
        call.call_status = "error"
        call.disconnection_reason = "error_telephony"
        await session.commit()
        # Non-2xx: consumers treat this as call-not-placed.
        raise HTTPException(500, detail="Failed to initiate call")

    return call_to_dict(call)


@router.get("/v2/get-call/{call_id}")
async def get_call(
    call_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    call = await session.get(Call, call_id)
    if call is None or call.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Call not found")
    out = serialize_call(call)
    # Detail Logs are dashboard-only (a reconstruction from lifecycle +
    # webhook-delivery bookkeeping), so they're attached here rather than in the
    # shared serializer that also builds outbound webhook payloads.
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


@router.post("/v2/list-calls")
async def list_calls(
    body: ListCallsRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    q = select(Call).where(Call.workspace_id == api_key.workspace_id)
    fc = body.filter_criteria or {}
    if agent_ids := fc.get("agent_id"):
        q = q.where(Call.agent_id.in_(agent_ids))
    if statuses := fc.get("call_status"):
        q = q.where(Call.call_status.in_(statuses))
    if directions := fc.get("direction"):
        q = q.where(Call.direction.in_(directions))
    if sentiments := fc.get("user_sentiment"):
        q = q.where(Call.call_analysis["user_sentiment"].as_string().in_(sentiments))
    if from_numbers := fc.get("from_number"):
        q = q.where(Call.from_number.in_(from_numbers))
    if to_numbers := fc.get("to_number"):
        q = q.where(Call.to_number.in_(to_numbers))
    if start_ts := fc.get("start_timestamp"):
        if lower := start_ts.get("lower_threshold"):
            q = q.where(Call.start_timestamp >= lower)
        if upper := start_ts.get("upper_threshold"):
            q = q.where(Call.start_timestamp <= upper)

    # Order and paginate on (created_at_ms, call_id): create-batch-call inserts
    # many rows in the same millisecond, so a created_at_ms-only anchor would
    # skip every sibling of the anchor row.
    ascending = body.sort_order == "ascending"
    if body.pagination_key:
        anchor = await session.get(Call, body.pagination_key)
        if anchor is not None:
            key = tuple_(Call.created_at_ms, Call.call_id)
            bound = (anchor.created_at_ms, anchor.call_id)
            q = q.where(key > bound if ascending else key < bound)

    if ascending:
        q = q.order_by(Call.created_at_ms.asc(), Call.call_id.asc())
    else:
        q = q.order_by(Call.created_at_ms.desc(), Call.call_id.desc())
    rows = (await session.scalars(q.limit(body.limit))).all()
    return [serialize_call(c) for c in rows]


@router.post("/v2/register-phone-call", status_code=201)
async def register_phone_call(
    body: RegisterPhoneCallRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Register a phone call for custom telephony — no dial is performed."""
    agent = await _get_workspace_agent(session, api_key.workspace_id, body.agent_id)

    call = Call(
        workspace_id=api_key.workspace_id,
        call_type="phone_call",
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_name=agent.agent_name,
        call_status="registered",
        direction=body.direction or "inbound",
        from_number=body.from_number,
        to_number=body.to_number,
        metadata_=body.metadata,
        retell_llm_dynamic_variables=_coerce_dynamic_variables(body.retell_llm_dynamic_variables),
    )
    session.add(call)
    await session.commit()
    return call_to_dict(call)


@router.post("/v2/create-web-call", status_code=201)
async def create_web_call(
    body: CreateWebCallRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_workspace_agent(session, api_key.workspace_id, body.agent_id)

    # Same 429 gate as create-phone-call — consumers match /concurrency limit|429/i.
    await concurrency.expire_stale_web_calls(session, api_key.workspace_id)
    if await concurrency.count_live_calls(session, api_key.workspace_id) >= (
        concurrency.CONCURRENCY_LIMIT
    ):
        raise HTTPException(
            429, detail=f"Concurrency limit reached ({concurrency.CONCURRENCY_LIMIT})"
        )

    call = Call(
        call_id=new_call_id(),
        workspace_id=api_key.workspace_id,
        call_type="web_call",
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_name=agent.agent_name,
        call_status="registered",
        direction="inbound",  # not exposed for web calls; column is non-null
        metadata_=body.metadata,
        retell_llm_dynamic_variables=_coerce_dynamic_variables(body.retell_llm_dynamic_variables),
    )
    call.livekit_room = telephony.room_name(call)
    call.access_token = _web_call_access_token(call)
    session.add(call)
    await session.commit()

    try:
        await telephony.dispatch_agent(call)
    except Exception:
        log.exception("failed to dispatch agent for web call %s", call.call_id)
        call.call_status = "error"
        call.disconnection_reason = "error_telephony"
        await session.commit()
        raise HTTPException(500, detail="Failed to start web call agent")

    settings = get_settings()
    public_url = settings.public_livekit_url
    if not public_url:
        global _warned_public_livekit_fallback
        if not _warned_public_livekit_fallback:
            _warned_public_livekit_fallback = True
            log.warning(
                "ARHITEQ_PUBLIC_LIVEKIT_URL is not set; returning LIVEKIT_URL (%s) as "
                "livekit_server_url — fine for local dev, wrong (and internal) anywhere else",
                settings.livekit_url,
            )
        public_url = settings.livekit_url
    out = web_call_to_dict(call)
    # Arhiteq extra (contract-safe): where the browser should connect.
    out["livekit_server_url"] = public_url
    return out


@router.patch("/v2/update-call/{call_id}")
async def update_call(
    call_id: str,
    body: UpdateCallRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    call = await session.get(Call, call_id)
    if call is None or call.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Call not found")
    # Only metadata and dynamic variables are mutable post-creation.
    if body.metadata is not None:
        call.metadata_ = body.metadata
    if body.retell_llm_dynamic_variables is not None:
        call.retell_llm_dynamic_variables = _coerce_dynamic_variables(
            body.retell_llm_dynamic_variables
        )
    await session.commit()
    return serialize_call(call)


@router.delete("/v2/delete-call/{call_id}", status_code=204)
async def delete_call(
    call_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    call = await session.get(Call, call_id)
    if call is None or call.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Call not found")
    await session.delete(call)
    await session.commit()


@router.put("/rerun-call-analysis/{call_id}", status_code=201)
async def rerun_call_analysis(
    call_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    call = await session.get(Call, call_id)
    if call is None or call.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Call not found")
    call.call_analysis = await analysis.analyze_call(
        transcript=call.transcript,
        direction=call.direction,
        duration_ms=call.duration_ms,
        disconnection_reason=call.disconnection_reason,
    )
    await session.commit()
    return serialize_call(call)
