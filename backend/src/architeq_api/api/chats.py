import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..config import get_settings
from ..db import get_session
from ..models import Agent, ApiKey, Chat, RetellLLM, now_ms
from ..schemas_extra import (
    CreateChatCompletionRequest,
    CreateChatRequest,
    ListChatsRequest,
    chat_to_dict,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["chats"])

_FALLBACK_REPLY = "Thanks for reaching out! How can I help you today?"

_CHAT_PROMPT = """\
{general_prompt}

You are chatting with a user over text. Reply with the agent's next message
only — plain text, no markdown, no role prefix.

Conversation so far:
{history}
Agent:"""


def _message(role: str, content: str) -> dict[str, Any]:
    return {
        "message_id": f"msg_{secrets.token_hex(16)}",
        "role": role,
        "content": content,
        "created_timestamp": now_ms(),
    }


async def _agent_reply(chat: Chat, session: AsyncSession) -> str:
    """Generate the agent's next turn via Gemini; canned reply without a key."""
    settings = get_settings()
    if not settings.google_api_key:
        return _FALLBACK_REPLY

    general_prompt = "You are a helpful assistant."
    agent = await session.get(Agent, chat.agent_id)
    if agent and (llm_id := (agent.response_engine or {}).get("llm_id")):
        llm = await session.get(RetellLLM, llm_id)
        if llm and llm.general_prompt:
            general_prompt = llm.general_prompt
            for key, value in (chat.retell_llm_dynamic_variables or {}).items():
                general_prompt = general_prompt.replace("{{" + key + "}}", str(value))

    history = "".join(
        f"{'Agent' if m.get('role') == 'agent' else 'User'}: {m.get('content', '')}\n"
        for m in (chat.messages or [])
    )
    try:
        from google import genai

        client = genai.Client(api_key=settings.google_api_key)
        resp = await client.aio.models.generate_content(
            model=settings.analysis_model,
            contents=_CHAT_PROMPT.format(general_prompt=general_prompt, history=history),
            config={"temperature": 0.3},
        )
        return (resp.text or "").strip() or _FALLBACK_REPLY
    except Exception:  # noqa: BLE001
        log.exception("chat completion failed for %s", chat.chat_id)
        return _FALLBACK_REPLY


@router.post("/create-chat", status_code=201)
async def create_chat(
    body: CreateChatRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, body.agent_id)
    if agent is None or agent.workspace_id != api_key.workspace_id:
        raise HTTPException(422, detail=f"agent {body.agent_id} not found")

    chat = Chat(
        workspace_id=api_key.workspace_id,
        agent_id=agent.agent_id,
        agent_version=agent.version,
        chat_status="ongoing",
        messages=[],
        metadata_=body.metadata,
        retell_llm_dynamic_variables={
            str(k): str(v) for k, v in (body.retell_llm_dynamic_variables or {}).items()
        },
    )
    session.add(chat)
    await session.commit()
    return chat_to_dict(chat)


@router.get("/get-chat/{chat_id}")
async def get_chat(
    chat_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Chat not found")
    return chat_to_dict(chat)


@router.get("/list-chat")
async def list_chat(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(Chat)
            .where(Chat.workspace_id == api_key.workspace_id)
            .order_by(Chat.created_at_ms.desc())
        )
    ).all()
    return [chat_to_dict(c) for c in rows]


@router.post("/v3/list-chats")
async def list_chats_v3(
    body: ListChatsRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    q = select(Chat).where(Chat.workspace_id == api_key.workspace_id)
    fc = body.filter_criteria or {}
    if agent_ids := fc.get("agent_id"):
        q = q.where(Chat.agent_id.in_(agent_ids))
    if statuses := fc.get("chat_status"):
        values = statuses.get("value") if isinstance(statuses, dict) else statuses
        if values:
            q = q.where(Chat.chat_status.in_(values))
    if body.pagination_key:
        anchor = await session.get(Chat, body.pagination_key)
        if anchor is not None:
            if body.sort_order == "ascending":
                q = q.where(Chat.created_at_ms > anchor.created_at_ms)
            else:
                q = q.where(Chat.created_at_ms < anchor.created_at_ms)
    order = (
        Chat.created_at_ms.asc() if body.sort_order == "ascending" else Chat.created_at_ms.desc()
    )
    rows = (await session.scalars(q.order_by(order).limit(body.limit + 1))).all()
    has_more = len(rows) > body.limit
    rows = rows[: body.limit]
    return {
        "items": [chat_to_dict(c) for c in rows],
        "has_more": has_more,
        "next_pagination_key": rows[-1].chat_id if has_more and rows else None,
    }


@router.post("/create-chat-completion", status_code=201)
async def create_chat_completion(
    body: CreateChatCompletionRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    chat = await session.get(Chat, body.chat_id)
    if chat is None or chat.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Chat not found")
    if chat.chat_status != "ongoing":
        raise HTTPException(422, detail="Chat has ended")

    user_message = _message("user", body.content)
    chat.messages = (chat.messages or []) + [user_message]
    agent_message = _message("agent", await _agent_reply(chat, session))
    chat.messages = chat.messages + [agent_message]
    await session.commit()
    # Retell returns only the messages generated during this completion.
    return {"messages": [agent_message]}


@router.patch("/end-chat/{chat_id}", status_code=204)
async def end_chat(
    chat_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    chat = await session.get(Chat, chat_id)
    if chat is None or chat.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Chat not found")
    chat.chat_status = "ended"
    chat.end_timestamp = now_ms()
    await session.commit()
