from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import Agent, ApiKey, now_ms
from ..schemas import CreateAgentRequest, agent_to_dict

router = APIRouter(tags=["agents"])

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
}


@router.post("/create-agent", status_code=201)
async def create_agent(
    body: CreateAgentRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    data = body.model_dump(exclude_none=True, exclude={"agent_id"})
    agent = Agent(
        workspace_id=api_key.workspace_id,
        **{k: v for k, v in data.items() if k in _MUTABLE_FIELDS or k == "response_engine"},
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
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Agent not found")
    return agent_to_dict(agent)


@router.get("/list-agents")
async def list_agents(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(select(Agent).where(Agent.workspace_id == api_key.workspace_id))
    ).all()
    return [agent_to_dict(a) for a in rows]


@router.patch("/update-agent/{agent_id}")
async def update_agent(
    agent_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Agent not found")
    payload = await request.json()
    for field, value in payload.items():
        if field in _MUTABLE_FIELDS:
            setattr(agent, field, value)
    agent.version += 1
    agent.last_modification_timestamp = now_ms()
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
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Agent not found")
    return [agent_to_dict(agent)]


@router.post("/publish-agent/{agent_id}")
async def publish_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Agent not found")
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
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Agent not found")
    await session.delete(agent)
    await session.commit()
