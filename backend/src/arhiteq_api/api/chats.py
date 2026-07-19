import logging
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..config import get_settings
from ..db import get_session
from ..models import Agent, ApiKey, Chat, RetellLLM, now_ms
from ..services.gemini import build_genai_client, genai_credentials_available, is_live_model
from ..services.template_variables import ChatVariables, resolve_template
from ..schemas_extra import (
    CreateChatCompletionRequest,
    CreateChatRequest,
    ListChatsRequest,
    chat_to_dict,
)
from ._deps import get_owned

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


def _resolve_chat_prompt(general_prompt: str, chat: Chat) -> str:
    # Retell chat system variables (chat_id, session_type, session_duration,
    # the current_time family) resolve underneath user-supplied dynamic
    # variables, with the same template semantics as voice calls.
    variables = ChatVariables(
        {str(k): str(v) for k, v in (chat.retell_llm_dynamic_variables or {}).items()},
        chat_id=chat.chat_id,
        start_timestamp_ms=chat.start_timestamp,
    )
    return resolve_template(general_prompt, variables)


async def _agent_reply(chat: Chat, session: AsyncSession) -> tuple[str, bool]:
    """Generate the agent's next turn via Gemini.

    Returns (reply, used_fallback). used_fallback is True when the reply is the
    canned placeholder (no creds, model error, or empty response) rather than a
    real model turn — so the Test-LLM UI can flag it instead of passing a fake
    answer off as success.
    """
    settings = get_settings()
    if not genai_credentials_available(settings):
        return _FALLBACK_REPLY, True

    general_prompt = "You are a helpful assistant."
    # Test the agent's own text model; Live (native-audio) models can't serve
    # generate_content, so fall back to the platform analysis model for those.
    model = settings.analysis_model
    agent = await session.get(Agent, chat.agent_id)
    if agent and (llm_id := (agent.response_engine or {}).get("llm_id")):
        llm = await session.get(RetellLLM, llm_id)
        if llm:
            if llm.general_prompt:
                general_prompt = _resolve_chat_prompt(llm.general_prompt, chat)
            if llm.model and not is_live_model(llm.model):
                model = llm.model

    history = "".join(
        f"{'Agent' if m.get('role') == 'agent' else 'User'}: {m.get('content', '')}\n"
        for m in (chat.messages or [])
    )
    try:
        client = build_genai_client(settings)
        resp = await client.aio.models.generate_content(
            model=model,
            contents=_CHAT_PROMPT.format(general_prompt=general_prompt, history=history),
            config={"temperature": 0.3},
        )
        text = (resp.text or "").strip()
        return (text, False) if text else (_FALLBACK_REPLY, True)
    except Exception:  # noqa: BLE001
        log.exception("chat completion failed for %s", chat.chat_id)
        return _FALLBACK_REPLY, True


@router.post("/create-chat", status_code=201)
async def create_chat(
    body: CreateChatRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    agent = await get_owned(
        session,
        Agent,
        body.agent_id,
        api_key.workspace_id,
        detail=f"agent {body.agent_id} not found",
        status=422,
    )

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
    chat = await get_owned(session, Chat, chat_id, api_key.workspace_id, detail="Chat not found")
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
    # Tie-break on chat_id so same-millisecond rows aren't skipped by the anchor.
    ascending = body.sort_order == "ascending"
    if body.pagination_key:
        anchor = await session.get(Chat, body.pagination_key)
        if anchor is not None:
            key = tuple_(Chat.created_at_ms, Chat.chat_id)
            bound = (anchor.created_at_ms, anchor.chat_id)
            q = q.where(key > bound if ascending else key < bound)
    if ascending:
        q = q.order_by(Chat.created_at_ms.asc(), Chat.chat_id.asc())
    else:
        q = q.order_by(Chat.created_at_ms.desc(), Chat.chat_id.desc())
    rows = (await session.scalars(q.limit(body.limit + 1))).all()
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
    chat = await get_owned(
        session, Chat, body.chat_id, api_key.workspace_id, detail="Chat not found"
    )
    if chat.chat_status != "ongoing":
        raise HTTPException(422, detail="Chat has ended")

    user_message = _message("user", body.content)
    chat.messages = (chat.messages or []) + [user_message]
    reply, used_fallback = await _agent_reply(chat, session)
    agent_message = _message("agent", reply)
    chat.messages = chat.messages + [agent_message]
    await session.commit()
    # Retell returns only the messages generated during this completion.
    # `is_fallback` is an Arhiteq-extra (additive) so the dashboard's Test-LLM
    # tab can flag a canned reply rather than render it as a real answer.
    result: dict[str, Any] = {"messages": [agent_message]}
    if used_fallback:
        result["is_fallback"] = True
    return result


@router.patch("/end-chat/{chat_id}", status_code=204)
async def end_chat(
    chat_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    chat = await get_owned(session, Chat, chat_id, api_key.workspace_id, detail="Chat not found")
    chat.chat_status = "ended"
    chat.end_timestamp = now_ms()
    await session.commit()
