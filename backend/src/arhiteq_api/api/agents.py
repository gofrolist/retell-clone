from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import (
    DEFAULT_WEBHOOK_TIMEOUT_MS,
    Agent,
    AgentFolder,
    ApiKey,
    PhoneNumber,
    Workspace,
    now_ms,
)
from ..schemas import (
    WEBHOOK_TIMEOUT_MS_MAX,
    WEBHOOK_TIMEOUT_MS_MIN,
    CreateAgentRequest,
    TestWebhookRequest,
    agent_to_dict,
    normalize_timezone,
    normalize_webhook_events,
)
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
    "pii_config",
    "fallback_voice_ids",
    "allow_user_dtmf",
    "allow_dtmf_interruption",
    "user_dtmf_options",
    "opt_in_signed_url",
    "ivr_option",
    "call_screening_option",
    "timezone",
    "response_engine",
    "folder_id",
}


def _require_object_body(payload: Any) -> None:
    """Reject a non-object PATCH body before the field validators index it.

    apply_patch makes the same check, but the validators below run first and
    would raise a TypeError (opaque 500) on a JSON array or string body.
    """
    if not isinstance(payload, Mapping):
        raise HTTPException(422, detail="Request body must be a JSON object")


def _validate_timezone_patch(payload: dict) -> None:
    """Validate + normalize the agent timezone on PATCH, in place."""
    if "timezone" not in payload:
        return
    value = payload["timezone"]
    if value is not None and not isinstance(value, str):
        raise HTTPException(422, detail="timezone must be an IANA timezone name or null")
    try:
        payload["timezone"] = normalize_timezone(value)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from None


def _validate_webhook_patch(payload: dict) -> None:
    """Validate + normalize the webhook overrides on PATCH, in place.

    apply_patch writes raw payload values straight onto the ORM object, so the
    PATCH path has to apply the same rules CreateAgentRequest's validators do.
    Both call the shared normalizers in schemas so they can't drift.
    """
    if "webhook_timeout_ms" in payload:
        t = payload["webhook_timeout_ms"]
        if t is not None:
            # Match Pydantic: bools are not ints, and a fractional float is not
            # a valid integer (but 5000.0 coerces to 5000).
            if isinstance(t, bool) or not isinstance(t, (int, float)) or t != int(t):
                raise HTTPException(422, detail="webhook_timeout_ms must be an integer")
            t = int(t)
            if not WEBHOOK_TIMEOUT_MS_MIN <= t <= WEBHOOK_TIMEOUT_MS_MAX:
                raise HTTPException(
                    422,
                    detail=(
                        f"webhook_timeout_ms must be between {WEBHOOK_TIMEOUT_MS_MIN} "
                        f"and {WEBHOOK_TIMEOUT_MS_MAX}"
                    ),
                )
            payload["webhook_timeout_ms"] = t
    if "webhook_events" in payload:
        events = payload["webhook_events"]
        if events is not None:
            if not isinstance(events, list):
                raise HTTPException(422, detail="webhook_events must be a list or null")
            try:
                payload["webhook_events"] = normalize_webhook_events(events)
            except ValueError as exc:
                raise HTTPException(422, detail=str(exc)) from None


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
    _require_object_body(payload)
    _validate_webhook_patch(payload)
    _validate_timezone_patch(payload)
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
    return await webhooks.send_test_event(
        session,
        api_key.workspace_id,
        url=url,
        event=body.event,
        call=webhooks.sample_call(agent.workspace_id, agent),
        timeout_ms=(
            body.webhook_timeout_ms or agent.webhook_timeout_ms or DEFAULT_WEBHOOK_TIMEOUT_MS
        ),
    )


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
