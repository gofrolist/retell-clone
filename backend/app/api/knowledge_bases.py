"""Knowledge base CRUD. Storage only — retrieval/embedding is TODO.

Retell's create/add-sources endpoints are multipart (they accept file
uploads); JSON bodies are also accepted for convenience since the shapes are
identical minus files.
"""

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_api_key
from ..db import get_session
from ..ids import new_source_id
from ..models import ApiKey, KnowledgeBase, now_ms
from ..schemas_extra import knowledge_base_to_dict

log = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge-bases"])


async def _parse_body(request: Request) -> dict[str, Any]:
    """Accept multipart/form-data (Retell's wire format) or plain JSON."""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return await request.json()

    form = await request.form()
    out: dict[str, Any] = {}
    if name := form.get("knowledge_base_name"):
        out["knowledge_base_name"] = name
    texts: list[dict[str, Any]] = []
    for raw in form.getlist("knowledge_base_texts"):
        if not isinstance(raw, str):
            continue
        try:
            texts.append(json.loads(raw))
        except ValueError as exc:
            raise HTTPException(422, detail="knowledge_base_texts must be JSON objects") from exc
    if texts:
        out["knowledge_base_texts"] = texts
    if urls := [u for u in form.getlist("knowledge_base_urls") if isinstance(u, str)]:
        out["knowledge_base_urls"] = urls
    files = [f for f in form.getlist("knowledge_base_files") if not isinstance(f, str)]
    if files:
        out["knowledge_base_files"] = [f.filename for f in files]
    return out


def _build_sources(data: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for text in data.get("knowledge_base_texts") or []:
        title = text.get("title")
        content = text.get("text")
        if not title or content is None:
            raise HTTPException(422, detail="knowledge_base_texts items need title and text")
        sources.append(
            {
                "type": "text",
                "source_id": new_source_id(),
                "title": title,
                "content": content,
            }
        )
    for url in data.get("knowledge_base_urls") or []:
        sources.append({"type": "url", "source_id": new_source_id(), "url": url})
    for filename in data.get("knowledge_base_files") or []:
        # TODO: persist uploaded file content; we currently record metadata only.
        sources.append(
            {"type": "document", "source_id": new_source_id(), "filename": filename}
        )
    return sources


async def _get_workspace_kb(
    session: AsyncSession, workspace_id: str, knowledge_base_id: str
) -> KnowledgeBase:
    kb = await session.get(KnowledgeBase, knowledge_base_id)
    if kb is None or kb.workspace_id != workspace_id:
        raise HTTPException(404, detail="Knowledge base not found")
    return kb


@router.post("/create-knowledge-base", status_code=201)
async def create_knowledge_base(
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    data = await _parse_body(request)
    name = data.get("knowledge_base_name")
    if not name:
        raise HTTPException(422, detail="knowledge_base_name is required")

    kb = KnowledgeBase(
        workspace_id=api_key.workspace_id,
        knowledge_base_name=name,
        sources=_build_sources(data),
        # No async ingestion pipeline yet: sources are stored synchronously,
        # so the knowledge base is immediately complete.
        status="complete",
        enable_auto_refresh=bool(data.get("enable_auto_refresh", False)),
    )
    session.add(kb)
    await session.commit()
    return knowledge_base_to_dict(kb)


@router.get("/get-knowledge-base/{knowledge_base_id}")
async def get_knowledge_base(
    knowledge_base_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_workspace_kb(session, api_key.workspace_id, knowledge_base_id)
    return knowledge_base_to_dict(kb)


@router.get("/list-knowledge-bases")
async def list_knowledge_bases(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.scalars(
            select(KnowledgeBase).where(KnowledgeBase.workspace_id == api_key.workspace_id)
        )
    ).all()
    return [knowledge_base_to_dict(kb) for kb in rows]


@router.delete("/delete-knowledge-base/{knowledge_base_id}", status_code=204)
async def delete_knowledge_base(
    knowledge_base_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_workspace_kb(session, api_key.workspace_id, knowledge_base_id)
    await session.delete(kb)
    await session.commit()


@router.post("/add-knowledge-base-sources/{knowledge_base_id}", status_code=201)
async def add_knowledge_base_sources(
    knowledge_base_id: str,
    request: Request,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_workspace_kb(session, api_key.workspace_id, knowledge_base_id)
    data = await _parse_body(request)
    kb.sources = (kb.sources or []) + _build_sources(data)
    kb.last_refreshed_timestamp = now_ms()
    await session.commit()
    return knowledge_base_to_dict(kb)


@router.delete(
    "/delete-knowledge-base-source/{knowledge_base_id}/source/{source_id}", status_code=204
)
async def delete_knowledge_base_source(
    knowledge_base_id: str,
    source_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_workspace_kb(session, api_key.workspace_id, knowledge_base_id)
    remaining = [s for s in (kb.sources or []) if s.get("source_id") != source_id]
    if len(remaining) == len(kb.sources or []):
        raise HTTPException(404, detail="Source not found")
    kb.sources = remaining
    kb.last_refreshed_timestamp = now_ms()
    await session.commit()
