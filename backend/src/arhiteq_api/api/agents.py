import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import security, signature
from ..auth import require_api_key
from ..config import get_settings
from ..db import get_session
from ..models import (
    WEBHOOK_EVENT_TYPES,
    Agent,
    AgentFolder,
    ApiKey,
    Call,
    PhoneNumber,
    Workspace,
    now_ms,
)
from ..schemas import CreateAgentRequest, TestWebhookRequest, agent_to_dict, call_to_dict
from ..services import webhooks
from ._deps import apply_patch, get_owned
from .chat_agents import CHAT_VOICE_ID

router = APIRouter(tags=["agents"])


async def _get_voice_agent(session, agent_id: str, workspace_id: str) -> Agent:
    """Workspace-scoped agent lookup that excludes chat agents.

    Chat agents are Agent rows flagged by voice_id == CHAT_VOICE_ID and live
    behind the /chat-agent endpoints; the voice-agent API must not surface them.
    """
    agent = await get_owned(session, Agent, agent_id, workspace_id, detail="Agent not found")
    if agent.voice_id == CHAT_VOICE_ID:
        raise HTTPException(404, detail="Agent not found")
    return agent


_MUTABLE_FIELDS = {
    "agent_name",
    "voice_id",
    "voice_model",
    "voice_temperature",
    "voice_speed",
    "volume",
    "language",
    "responsiveness",
    "interruption_sensitivity",
    "enable_backchannel",
    "backchannel_frequency",
    "backchannel_words",
    "reminder_trigger_ms",
    "reminder_max_count",
    "ambient_sound",
    "ambient_sound_volume",
    "webhook_url",
    "webhook_timeout_ms",
    "webhook_events",
    "boosted_keywords",
    "pronunciation_dictionary",
    "normalize_for_speech",
    "end_call_after_silence_ms",
    "max_call_duration_ms",
    "voicemail_option",
    "enable_voicemail_detection",
    "post_call_analysis_data",
    "post_call_analysis_model",
    "begin_message_delay_ms",
    "stt_mode",
    "denoising_mode",
    "opt_out_sensitive_data_storage",
    "response_engine",
    "folder_id",
}


def _validate_webhook_patch(payload: dict) -> None:
    """Guard the webhook overrides on PATCH (create goes through Pydantic).

    apply_patch writes raw payload values straight onto the ORM object, so the
    same bounds CreateAgentRequest enforces have to be re-checked here.
    """
    if "webhook_timeout_ms" in payload:
        t = payload["webhook_timeout_ms"]
        if t is not None and (
            not isinstance(t, int) or isinstance(t, bool) or not 1000 <= t <= 30000
        ):
            raise HTTPException(422, detail="webhook_timeout_ms must be between 1000 and 30000")
    if "webhook_events" in payload:
        events = payload["webhook_events"]
        if events is not None:
            if not isinstance(events, list):
                raise HTTPException(422, detail="webhook_events must be a list or null")
            unknown = [e for e in events if e not in WEBHOOK_EVENT_TYPES]
            if unknown:
                raise HTTPException(
                    422,
                    detail=(
                        f"unknown webhook event(s): {', '.join(map(str, unknown))}; "
                        f"allowed: {', '.join(WEBHOOK_EVENT_TYPES)}"
                    ),
                )


async def _validate_folder_id(session, folder_id, workspace_id: str) -> None:
    """422 unless folder_id is null or names a folder in this workspace."""
    if folder_id is None:
        return
    folder = await session.get(AgentFolder, folder_id)
    if folder is None or folder.workspace_id != workspace_id:
        raise HTTPException(422, detail="folder_id does not reference a folder in this workspace")


@router.post("/create-agent", status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    await _validate_folder_id(session, body.folder_id, api_key.workspace_id)
    data = body.model_dump(exclude_none=True, exclude={"agent_id"})
    agent = Agent(
        workspace_id=api_key.workspace_id,
        **{k: v for k, v in data.items() if k in _MUTABLE_FIELDS},
    )
    if body.agent_id:  # import mode: preserve an existing Retell agent id
        if await session.get(Agent, body.agent_id) is not None:
            raise HTTPException(409, detail="agent_id already exists")
        agent.agent_id = body.agent_id
    session.add(agent)
    await session.commit()
    return agent_to_dict(agent)


@router.get("/get-agent/{agent_id}")
async def get_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    return agent_to_dict(agent)


@router.get("/list-agents")
async def list_agents(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(Agent).where(
                Agent.workspace_id == api_key.workspace_id,
                # Exclude chat agents (voice_id == CHAT_VOICE_ID); is_distinct_from
                # keeps rows with a NULL voice_id.
                Agent.voice_id.is_distinct_from(CHAT_VOICE_ID),
            )
        )
    ).all()
    return [agent_to_dict(a) for a in rows]


@router.patch("/update-agent/{agent_id}")
async def update_agent(
    agent_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    payload = await request.json()
    _validate_webhook_patch(payload)
    if "folder_id" in payload:
        await _validate_folder_id(session, payload["folder_id"], api_key.workspace_id)
    # A folder move is a dashboard-only regrouping, not a config change: it
    # must not mint a new agent version (call records stamp agent_version).
    config_change = bool((set(payload) & _MUTABLE_FIELDS) - {"folder_id"})
    apply_patch(agent, payload, _MUTABLE_FIELDS, bump_version=config_change, touch=config_change)
    await session.commit()
    return agent_to_dict(agent)


@router.get("/get-agent-versions/{agent_id}")
async def get_agent_versions(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    # Single live version per agent (no version history table yet), so the
    # version list is the current agent object alone.
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    return [agent_to_dict(agent)]


@router.post("/publish-agent/{agent_id}")
async def publish_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    agent.is_published = True
    agent.last_modification_timestamp = now_ms()
    await session.commit()
    return agent_to_dict(agent)


def _sample_call_payload(agent: Agent) -> dict:
    """A representative, non-persisted call object for the Test button.

    Built via call_to_dict so the sample stays byte-identical in shape to a real
    delivery. Marked with metadata so consumers can drop it if they choose.
    """
    ts = now_ms()
    sample = Call(
        call_id="call_test_webhook",
        workspace_id=agent.workspace_id,
        agent_id=agent.agent_id,
        agent_version=agent.version,
        agent_name=agent.agent_name,
        call_type="web_call",
        call_status="ended",
        direction="outbound",
        from_number="+15551234567",
        to_number="+15557654321",
        metadata_={"arhiteq_test": True},
        start_timestamp=ts - 30_000,
        end_timestamp=ts,
        duration_ms=30_000,
        disconnection_reason="agent_hangup",
        transcript="Agent: This is a test webhook from Arhiteq.\nUser: Great, it works!",
        call_analysis={
            "call_summary": "Test webhook delivery from the Arhiteq dashboard.",
            "user_sentiment": "Positive",
            "call_successful": True,
            "in_voicemail": False,
        },
    )
    return call_to_dict(sample)


@router.post("/test-agent-webhook/{agent_id}")
async def test_agent_webhook(
    agent_id: str,
    body: TestWebhookRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Send one signed sample event to the agent's webhook URL and report back.

    Powers the dashboard "Test" button. Prefers the URL in the request (the
    on-screen, possibly-unsaved value) so users can validate before saving.
    """
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    url = (body.webhook_url or "").strip() or agent.webhook_url
    if not url:
        ws = await session.get(Workspace, agent.workspace_id)
        url = ws.webhook_url if ws is not None else None
    if not url:
        raise HTTPException(422, detail="No webhook URL configured to test")
    try:
        # DNS resolution is blocking; keep it off the event loop (and this is the
        # SSRF gate — the URL is user-supplied).
        await run_in_threadpool(security.assert_url_safe, url)
    except security.UnsafeUrlError as exc:
        raise HTTPException(422, detail=f"Refusing to send to unsafe URL: {exc}")

    key = await webhooks.signing_key(session, api_key.workspace_id)
    if key is None:
        raise HTTPException(409, detail="No active API key available to sign the webhook")

    raw_body = json.dumps(
        {"event": body.event, "call": _sample_call_payload(agent)}, separators=(",", ":")
    )
    timeout = (
        body.webhook_timeout_ms / 1000
        if body.webhook_timeout_ms
        else (
            agent.webhook_timeout_ms / 1000
            if agent.webhook_timeout_ms
            else get_settings().webhook_timeout_seconds
        )
    )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                url,
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    signature.SIGNATURE_HEADER: signature.sign(raw_body, key),
                },
            )
    except httpx.HTTPError as exc:
        return {"ok": False, "status_code": None, "error": str(exc)}
    ok = 200 <= resp.status_code < 300
    return {
        "ok": ok,
        "status_code": resp.status_code,
        "error": None if ok else f"HTTP {resp.status_code}",
    }


@router.delete("/delete-agent/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    # A phone number's inbound/outbound_agent_id FKs default to RESTRICT, so a
    # bound DID would turn the delete into an unhandled IntegrityError (500).
    # Fail loud and early with a 409 that names the DIDs so the caller knows to
    # repoint or release them first.
    bound = (
        await session.scalars(
            select(PhoneNumber.phone_number).where(
                PhoneNumber.workspace_id == api_key.workspace_id,
                (PhoneNumber.inbound_agent_id == agent_id)
                | (PhoneNumber.outbound_agent_id == agent_id),
            )
        )
    ).all()
    if bound:
        raise HTTPException(
            409,
            detail=(
                "Agent is still routed to phone number(s): "
                f"{', '.join(bound)}. Repoint or release them before deleting."
            ),
        )
    await session.delete(agent)
    await session.commit()
