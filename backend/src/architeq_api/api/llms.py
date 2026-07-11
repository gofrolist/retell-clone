from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..models import ApiKey, RetellLLM, now_ms
from ..schemas import CreateLLMRequest, llm_to_dict

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
    llm = await session.get(RetellLLM, llm_id)
    if llm is None or llm.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Retell LLM not found")
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
    llm = await session.get(RetellLLM, llm_id)
    if llm is None or llm.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Retell LLM not found")
    payload = await request.json()
    for field, value in payload.items():
        if field in _MUTABLE_FIELDS:
            setattr(llm, field, value)
    llm.version += 1
    llm.last_modification_timestamp = now_ms()
    await session.commit()
    return llm_to_dict(llm)


@router.delete("/delete-retell-llm/{llm_id}", status_code=204)
async def delete_llm(
    llm_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    llm = await session.get(RetellLLM, llm_id)
    if llm is None or llm.workspace_id != api_key.workspace_id:
        raise HTTPException(404, detail="Retell LLM not found")
    await session.delete(llm)
    await session.commit()
