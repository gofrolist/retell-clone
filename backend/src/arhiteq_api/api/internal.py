"""Worker-facing internal API. See docs/INTERNAL_API.md."""

import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_internal_token
from ..db import get_session, session_factory
from ..models import Agent, Call, Contact, PhoneNumber, RetellLLM, now_ms
from ..schemas import agent_to_dict, llm_to_dict
from ..services import inbound as inbound_svc
from ..services import webhooks
from ..services.analysis import analyze_call
from ..services.metrics import CALL_DURATION, CALLS_ONGOING, CALLS_TOTAL
from ..services.recordings import sign_recording_url

log = logging.getLogger(__name__)
router = APIRouter(
    prefix="/internal", tags=["internal"], dependencies=[Depends(require_internal_token)]
)


def _function_secret() -> str | None:
    # Shared secret the worker sends as X-Caller-Secret on custom tool calls
    # (consumer's RETELL_FUNCTION_SECRET). Per-workspace storage: TODO — env
    # is sufficient while Arhiteq serves a single tenant.
    return os.environ.get("ARHITEQ_FUNCTION_SECRET")


async def _call_config(call: Call, session: AsyncSession) -> dict[str, Any]:
    agent = await session.get(Agent, call.agent_id)
    if agent is None:
        raise HTTPException(500, detail=f"agent {call.agent_id} missing")
    llm_id = (agent.response_engine or {}).get("llm_id")
    llm = await session.get(RetellLLM, llm_id) if llm_id else None
    dyn: dict[str, str] = {}
    if llm is not None and llm.default_dynamic_variables:
        dyn.update({str(k): str(v) for k, v in llm.default_dynamic_variables.items()})
    dyn.update(call.retell_llm_dynamic_variables or {})
    return {
        "call_id": call.call_id,
        "direction": call.direction,
        "from_number": call.from_number,
        "to_number": call.to_number,
        # The worker gates phone-call-only system variables ({{direction}},
        # {{user_number}}, {{agent_number}}) on this.
        "call_type": call.call_type,
        "agent": agent_to_dict(agent),
        "llm": llm_to_dict(llm) if llm is not None else None,
        "dynamic_variables": dyn,
        "metadata": call.metadata_ or {},
        "function_secret": _function_secret(),
    }


@router.get("/calls/{call_id}/config")
async def get_call_config(call_id: str, session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail="Call not found")
    return await _call_config(call, session)


@router.get("/agents/{agent_id}/config")
async def get_agent_config(
    agent_id: str, call_id: str, session: AsyncSession = Depends(get_session)
):
    """Destination config for the agent_swap tool: the worker re-points the
    live session at this agent's prompt/tools/voice mid-call.

    call_id is required and scopes the lookup to the calling call's
    workspace — agent_id comes from user-editable tool config, so an
    unscoped lookup would let one workspace pull another's prompts and
    tool secrets.
    """
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail="Call not found")
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != call.workspace_id:
        raise HTTPException(404, detail="Agent not found")
    llm_id = (agent.response_engine or {}).get("llm_id")
    llm = await session.get(RetellLLM, llm_id) if llm_id else None
    return {
        "agent": agent_to_dict(agent),
        "llm": llm_to_dict(llm) if llm is not None else None,
    }


async def _contact_variables(
    session: AsyncSession, workspace_id: str, from_number: str
) -> dict[str, str]:
    """first_name/last_name/user_timezone of the caller's contact, if one exists.

    Merged BENEATH webhook-supplied dynamic variables — the contact only
    fills gaps, so consumers that already send first_name are unaffected.
    """
    # Contacts are stored as typed in the dashboard, so match the common
    # spellings of the caller id: E.164, bare digits, and the NANP national
    # form with or without the leading 1.
    digits = re.sub(r"\D", "", from_number)
    candidates = {from_number}
    if digits:
        candidates.update({digits, "+" + digits})
        if len(digits) == 10:
            candidates.update({"1" + digits, "+1" + digits})
        if len(digits) == 11 and digits.startswith("1"):
            candidates.add(digits[1:])
    contact = (
        await session.scalars(
            select(Contact)
            .where(Contact.workspace_id == workspace_id, Contact.phone_number.in_(candidates))
            .limit(1)
        )
    ).first()
    if contact is None:
        return {}
    return {
        key: value
        for key, value in (
            ("first_name", contact.first_name),
            ("last_name", contact.last_name),
            # Consumer prompts read {{user_timezone}} (Retell's nested
            # {{current_time_{{user_timezone}}}} pattern).
            ("user_timezone", contact.timezone),
        )
        if value
    }


class InboundResolveRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    from_number: str
    to_number: str
    room: str | None = None


@router.post("/inbound/resolve")
async def resolve_inbound(
    body: InboundResolveRequest, session: AsyncSession = Depends(get_session)
):
    number = await session.get(PhoneNumber, body.to_number)
    if number is None:
        raise HTTPException(404, detail=f"Unknown DID {body.to_number}")

    override_agent_id, dyn_vars = await inbound_svc.resolve_inbound(
        number, body.from_number, body.to_number, _function_secret()
    )
    # Caller facts fill gaps beneath webhook-supplied variables: {{phone}} is
    # used in tool arguments by consumer prompts, so without a webhook it must
    # still resolve to the caller id. An empty-string webhook value never
    # erases a known caller fact. user_timezone always resolves so prompts can
    # rely on {{current_time_{{user_timezone}}}} (Retell's un-suffixed time
    # variables default to America/Los_Angeles too).
    base_vars = {"phone": body.from_number, "user_timezone": "America/Los_Angeles"}
    base_vars.update(await _contact_variables(session, number.workspace_id, body.from_number))
    merged = dict(base_vars)
    merged.update({k: v for k, v in dyn_vars.items() if v != "" or k not in base_vars})
    dyn_vars = merged
    agent_id = override_agent_id or number.inbound_agent_id
    if not agent_id:
        raise HTTPException(404, detail=f"No inbound agent for {body.to_number}")
    agent = await session.get(Agent, agent_id)
    if agent is None:
        # Webhook returned an unknown agent — degrade to the DID default.
        agent = await session.get(Agent, number.inbound_agent_id or "")
        if agent is None:
            raise HTTPException(404, detail="No usable inbound agent")

    call = Call(
        workspace_id=number.workspace_id,
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_name=agent.agent_name,
        direction="inbound",
        call_status="registered",
        from_number=body.from_number,
        to_number=body.to_number,
        retell_llm_dynamic_variables=dyn_vars,
        livekit_room=body.room,
    )
    session.add(call)
    await session.commit()
    return await _call_config(call, session)


class CallEventRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    event: str
    start_timestamp: int | None = None
    transcript: str | None = None
    transcript_object: list[Any] | None = None


@router.post("/calls/{call_id}/events")
async def post_call_event(
    call_id: str, body: CallEventRequest, session: AsyncSession = Depends(get_session)
):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail="Call not found")

    if body.event == "call_started":
        call.call_status = "ongoing"
        call.start_timestamp = body.start_timestamp or now_ms()
        await session.commit()
        CALLS_ONGOING.inc()
        webhooks.fire_and_forget(_send_webhook_fresh(call_id, "call_started"))
    elif body.event == "transcript_update":
        call.transcript = body.transcript
        if body.transcript_object is not None:
            call.transcript_object = body.transcript_object
        await session.commit()
    else:
        raise HTTPException(422, detail=f"Unknown event {body.event}")
    return {"ok": True}


class FinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    end_timestamp: int | None = None
    duration_ms: int | None = None
    disconnection_reason: str | None = None
    call_status: str = "ended"
    transcript: str | None = None
    transcript_object: list[Any] | None = None
    transcript_with_tool_calls: list[Any] | None = None
    recording_url: str | None = None
    in_voicemail: bool | None = None
    latency: dict[str, Any] | None = None
    collected_dynamic_variables: dict[str, Any] | None = None


@router.post("/calls/{call_id}/finalize")
async def finalize_call(
    call_id: str, body: FinalizeRequest, session: AsyncSession = Depends(get_session)
):
    call = await session.get(Call, call_id)
    if call is None:
        raise HTTPException(404, detail="Call not found")
    if call.call_status in ("ended", "error"):
        return {"ok": True, "idempotent": True}

    was_ongoing = call.call_status == "ongoing"
    call.call_status = body.call_status
    call.end_timestamp = body.end_timestamp or now_ms()
    call.duration_ms = body.duration_ms
    call.disconnection_reason = body.disconnection_reason
    # Only overwrite when the finalize actually carries the value: a crash-path
    # finalize with no transcript must not wipe what transcript_update events
    # already accumulated (the analysis pipeline reads call.transcript).
    if body.transcript is not None:
        call.transcript = body.transcript
    if body.transcript_object is not None:
        call.transcript_object = body.transcript_object
    if body.transcript_with_tool_calls is not None:
        call.transcript_with_tool_calls = body.transcript_with_tool_calls
    if body.recording_url is not None:
        # The bucket is private — store a signed URL, not the raw object URL.
        call.recording_url = await sign_recording_url(body.recording_url)
    if body.latency is not None:
        call.latency = body.latency
    if body.collected_dynamic_variables is not None:
        call.collected_dynamic_variables = body.collected_dynamic_variables
    await session.commit()

    if was_ongoing:
        CALLS_ONGOING.dec()
    CALLS_TOTAL.labels(direction=call.direction, status=body.call_status).inc()
    if body.duration_ms:
        CALL_DURATION.observe(body.duration_ms / 1000)

    webhooks.fire_and_forget(_finalize_pipeline(call_id, body.in_voicemail))
    return {"ok": True}


async def _send_webhook_fresh(call_id: str, event: str) -> None:
    """Load the call in a fresh session (the request session is closed by now)."""
    async with session_factory()() as session:
        call = await session.get(Call, call_id)
        if call is not None:
            await webhooks.send_event(session, call, event)


async def _finalize_pipeline(call_id: str, in_voicemail_hint: bool | None) -> None:
    """call_ended → Gemini analysis → call_analyzed. Runs post-response."""
    async with session_factory()() as session:
        call = await session.get(Call, call_id)
        if call is None:
            return
        await webhooks.send_event(session, call, "call_ended")

        analysis = await analyze_call(
            call.transcript,
            call.direction,
            call.duration_ms,
            call.disconnection_reason,
            in_voicemail_hint,
        )
        call.call_analysis = analysis
        await session.commit()
        await webhooks.send_event(session, call, "call_analyzed")
