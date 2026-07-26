from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, RetellLLM
from ..schemas import CreateLLMRequest, llm_to_dict
from ..services import versions
from ._deps import apply_patch, get_owned

router = APIRouter(tags=["retell-llm"])

_MUTABLE_FIELDS = {
    "model",
    "model_temperature",
    "general_prompt",
    "general_tools",
    "states",
    "starting_state",
    "begin_message",
    "start_speaker",
    "default_dynamic_variables",
    "knowledge_base_ids",
    "mcps",
}


@router.post("/create-retell-llm", status_code=201)
async def create_llm(
    body: CreateLLMRequest,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    data = {k: v for k, v in body.model_dump(exclude_none=True).items() if k in _MUTABLE_FIELDS}
    llm = RetellLLM(workspace_id=api_key.workspace_id, **data)
    session.add(llm)
    await session.commit()
    return llm_to_dict(llm)


@router.get("/get-retell-llm/{llm_id}")
async def get_llm(
    llm_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    llm = await get_owned(
        session, RetellLLM, llm_id, api_key.workspace_id, detail="Retell LLM not found"
    )
    return llm_to_dict(llm)


@router.get("/list-retell-llms")
async def list_llms(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(RetellLLM).where(RetellLLM.workspace_id == api_key.workspace_id)
        )
    ).all()
    return [llm_to_dict(x) for x in rows]


@router.patch("/update-retell-llm/{llm_id}")
async def update_llm(
    llm_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    llm = await get_owned(
        session, RetellLLM, llm_id, api_key.workspace_id, detail="Retell LLM not found"
    )
    payload = await request.json()
    # The prompt is part of the agent's config, so a prompt edit opens the
    # owning agent's draft too — otherwise it would land on the live rows with
    # nothing in the version history to show for it. Seeding has to happen
    # *before* the patch, or the agent's initial snapshot would capture the
    # edit it is supposed to predate.
    owners = (
        await versions.agents_using_llm(session, api_key.workspace_id, llm_id)
        if set(payload) & _MUTABLE_FIELDS
        else []
    )
    for agent in owners:
        await versions.ensure_seeded(session, agent)
    apply_patch(llm, payload, _MUTABLE_FIELDS, bump_version=True, touch=True)
    for agent in owners:
        await versions.touch(session, agent)
    await session.commit()
    return llm_to_dict(llm)


@router.delete("/delete-retell-llm/{llm_id}", status_code=204)
async def delete_llm(
    llm_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    llm = await get_owned(
        session, RetellLLM, llm_id, api_key.workspace_id, detail="Retell LLM not found"
    )
    await session.delete(llm)
    await session.commit()
