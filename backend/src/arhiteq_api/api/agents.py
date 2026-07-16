from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import Agent, AgentFolder, ApiKey, now_ms
from ..schemas import CreateAgentRequest, agent_to_dict
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


@router.delete("/delete-agent/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await _get_voice_agent(session, agent_id, api_key.workspace_id)
    await session.delete(agent)
    await session.commit()
