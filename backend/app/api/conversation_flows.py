from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, ConversationFlow, now_ms
from ..schemas_extra import CreateConversationFlowRequest, conversation_flow_to_dict

router = APIRouter(tags=["conversation-flows"])

_MUTABLE_FIELDS = {
    "global_prompt",
    "nodes",
    "start_node_id",
    "start_speaker",
    "model_choice",
    "tools",
    "default_dynamic_variables",
}


async def _get_workspace_flow(
    session: AsyncSession, workspace_id: str, conversation_flow_id: str
) -> ConversationFlow:
    flow = await session.get(ConversationFlow, conversation_flow_id)
    if flow is None or flow.workspace_id != workspace_id:
        raise HTTPException(404, detail="Conversation flow not found")
    return flow


@router.post("/create-conversation-flow", status_code=201)
async def create_conversation_flow(
    body: CreateConversationFlowRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    start_node_id = body.start_node_id
    if start_node_id is None and body.nodes:
        start_node_id = body.nodes[0].get("id")
    flow = ConversationFlow(
        workspace_id=api_key.workspace_id,
        global_prompt=body.global_prompt,
        nodes=body.nodes,
        start_node_id=start_node_id,
        start_speaker=body.start_speaker,
        model_choice=body.model_choice,
        tools=body.tools,
        default_dynamic_variables=body.default_dynamic_variables,
    )
    session.add(flow)
    await session.commit()
    return conversation_flow_to_dict(flow)


@router.get("/get-conversation-flow/{conversation_flow_id}")
async def get_conversation_flow(
    conversation_flow_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    flow = await _get_workspace_flow(session, api_key.workspace_id, conversation_flow_id)
    return conversation_flow_to_dict(flow)


@router.get("/v2/list-conversation-flows")
async def list_conversation_flows(
    limit: int = Query(default=50, le=1000),
    sort_order: str = Query(default="descending"),
    pagination_key: str | None = Query(default=None),
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    q = select(ConversationFlow).where(ConversationFlow.workspace_id == api_key.workspace_id)
    if pagination_key:
        anchor = await session.get(ConversationFlow, pagination_key)
        if anchor is not None:
            if sort_order == "ascending":
                q = q.where(ConversationFlow.created_at_ms > anchor.created_at_ms)
            else:
                q = q.where(ConversationFlow.created_at_ms < anchor.created_at_ms)
    order = (
        ConversationFlow.created_at_ms.asc()
        if sort_order == "ascending"
        else ConversationFlow.created_at_ms.desc()
    )
    # Fetch one extra row to compute has_more without a count query.
    rows = (await session.scalars(q.order_by(order).limit(limit + 1))).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {
        "items": [conversation_flow_to_dict(f) for f in rows],
        "pagination_key": rows[-1].conversation_flow_id if has_more and rows else None,
        "has_more": has_more,
    }


@router.patch("/update-conversation-flow/{conversation_flow_id}")
async def update_conversation_flow(
    conversation_flow_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    flow = await _get_workspace_flow(session, api_key.workspace_id, conversation_flow_id)
    payload = await request.json()
    for field, value in payload.items():
        if field in _MUTABLE_FIELDS:
            setattr(flow, field, value)
    flow.version += 1
    flow.last_modification_timestamp = now_ms()
    await session.commit()
    return conversation_flow_to_dict(flow)


@router.delete("/delete-conversation-flow/{conversation_flow_id}", status_code=204)
async def delete_conversation_flow(
    conversation_flow_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    flow = await _get_workspace_flow(session, api_key.workspace_id, conversation_flow_id)
    await session.delete(flow)
    await session.commit()
