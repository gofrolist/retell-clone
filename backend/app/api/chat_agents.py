"""Chat-agent CRUD (Retell `/create-chat-agent` family).

Chat agents reuse the Agent model — a chat agent is an agent whose
voice-specific settings are simply unused. Serialization mirrors Retell's
chat-agent object (agent fields minus voice/telephony concerns).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import Agent, ApiKey, now_ms
from ..schemas import CompatModel, ResponseEngine

router = APIRouter(tags=["chat-agents"])

# Chat agents are distinguished by this sentinel voice_id (they have no voice).
CHAT_VOICE_ID = "chat"

_MUTABLE_FIELDS = {"agent_name", "response_engine", "webhook_url", "language"}


class CreateChatAgentRequest(CompatModel):
    response_engine: ResponseEngine
    agent_name: str | None = None
    agent_id: str | None = None
    webhook_url: str | None = None
    language: str | None = None


def chat_agent_to_dict(agent: Agent) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "version": agent.version,
        "is_published": agent.is_published,
        "agent_type": "chat-agent",
        "response_engine": agent.response_engine,
        "agent_name": agent.agent_name,
        "language": agent.language,
        "webhook_url": agent.webhook_url,
        "last_modification_timestamp": agent.last_modification_timestamp,
    }


def _is_chat_agent(agent: Agent | None, workspace_id: str) -> bool:
    return (
        agent is not None
        and agent.workspace_id == workspace_id
        and agent.voice_id == CHAT_VOICE_ID
    )


@router.post("/create-chat-agent", status_code=201)
async def create_chat_agent(
    body: CreateChatAgentRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = Agent(
        workspace_id=api_key.workspace_id,
        agent_name=body.agent_name,
        response_engine=body.response_engine.model_dump(exclude_none=True),
        voice_id=CHAT_VOICE_ID,
        language=body.language or "en-US",
        webhook_url=body.webhook_url,
    )
    if body.agent_id:
        if await session.get(Agent, body.agent_id) is not None:
            raise HTTPException(409, detail="agent_id already exists")
        agent.agent_id = body.agent_id
    session.add(agent)
    await session.commit()
    return chat_agent_to_dict(agent)


@router.get("/get-chat-agent/{agent_id}")
async def get_chat_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if not _is_chat_agent(agent, api_key.workspace_id):
        raise HTTPException(404, detail="Chat agent not found")
    return chat_agent_to_dict(agent)


@router.get("/list-chat-agents")
async def list_chat_agents(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(Agent).where(
                Agent.workspace_id == api_key.workspace_id, Agent.voice_id == CHAT_VOICE_ID
            )
        )
    ).all()
    return [chat_agent_to_dict(a) for a in rows]


@router.patch("/update-chat-agent/{agent_id}")
async def update_chat_agent(
    agent_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if not _is_chat_agent(agent, api_key.workspace_id):
        raise HTTPException(404, detail="Chat agent not found")
    payload = await request.json()
    for field, value in payload.items():
        if field in _MUTABLE_FIELDS:
            setattr(agent, field, value)
    agent.version += 1
    agent.last_modification_timestamp = now_ms()
    await session.commit()
    return chat_agent_to_dict(agent)


@router.delete("/delete-chat-agent/{agent_id}", status_code=204)
async def delete_chat_agent(
    agent_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if not _is_chat_agent(agent, api_key.workspace_id):
        raise HTTPException(404, detail="Chat agent not found")
    await session.delete(agent)
    await session.commit()
