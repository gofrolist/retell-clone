"""Knowledge base CRUD. Storage only — retrieval/embedding is TODO.

Retell's create/add-sources endpoints are multipart (they accept file
uploads); JSON bodies are also accepted for convenience since the shapes are
identical minus files.
"""

import json
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete as sa_delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from ..auth import require_api_key
from ..config import get_settings
from ..db import get_session
from ..ids import new_knowledge_base_id, new_source_id
from ..models import ApiKey, KnowledgeBase, KnowledgeBaseFile, now_ms
from ..schemas_extra import knowledge_base_to_dict

log = logging.getLogger(__name__)
router = APIRouter(tags=["knowledge-bases"])

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB per uploaded file


def _file_url(knowledge_base_id: str, source_id: str) -> str:
    # Same convention as preview_audio_url: absolute when public_api_url is
    # configured, relative otherwise.
    base = get_settings().public_api_url.rstrip("/")
    return f"{base}/get-knowledge-base-file/{knowledge_base_id}/source/{source_id}"


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
    if files := [f for f in form.getlist("knowledge_base_files") if isinstance(f, UploadFile)]:
        out["knowledge_base_files"] = files
    return out


async def _build_sources(
    data: dict[str, Any], *, workspace_id: str, knowledge_base_id: str
) -> tuple[list[dict[str, Any]], list[KnowledgeBaseFile]]:
    """Normalize request payload into source dicts + file blob rows."""
    sources: list[dict[str, Any]] = []
    blobs: list[KnowledgeBaseFile] = []
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
    for file in data.get("knowledge_base_files") or []:
        if isinstance(file, str):
            # JSON bodies can only carry filenames; keep the legacy
            # metadata-only source for them.
            sources.append({"type": "document", "source_id": new_source_id(), "filename": file})
            continue
        content_bytes = await file.read()
        await file.close()
        if len(content_bytes) > MAX_FILE_BYTES:
            raise HTTPException(413, detail="File exceeds the 20MB limit")
        source_id = new_source_id()
        # Clamp to the column widths (String(255)/String(128) in models.py):
        # SQLite ignores VARCHAR lengths, but Postgres raises DataError on
        # overflow, so an over-long filename/content_type would 500 in prod
        # while passing tests unless we clamp before storing.
        filename = (file.filename or source_id)[:255]
        content_type = (file.content_type or "application/octet-stream")[:128]
        sources.append(
            {
                "type": "document",
                "source_id": source_id,
                "filename": filename,
                "file_size": len(content_bytes),
                "file_url": _file_url(knowledge_base_id, source_id),
            }
        )
        blobs.append(
            KnowledgeBaseFile(
                source_id=source_id,
                knowledge_base_id=knowledge_base_id,
                workspace_id=workspace_id,
                filename=filename,
                content_type=content_type,
                size_bytes=len(content_bytes),
                data=content_bytes,
            )
        )
    return sources, blobs


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

    knowledge_base_id = new_knowledge_base_id()
    sources, blobs = await _build_sources(
        data, workspace_id=api_key.workspace_id, knowledge_base_id=knowledge_base_id
    )
    kb = KnowledgeBase(
        knowledge_base_id=knowledge_base_id,
        workspace_id=api_key.workspace_id,
        knowledge_base_name=name,
        sources=sources,
        # No async ingestion pipeline yet: sources are stored synchronously,
        # so the knowledge base is immediately complete.
        status="complete",
        enable_auto_refresh=bool(data.get("enable_auto_refresh", False)),
    )
    session.add(kb)
    session.add_all(blobs)
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
    await session.execute(
        sa_delete(KnowledgeBaseFile).where(
            KnowledgeBaseFile.knowledge_base_id == kb.knowledge_base_id
        )
    )
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
    sources, blobs = await _build_sources(
        data, workspace_id=api_key.workspace_id, knowledge_base_id=kb.knowledge_base_id
    )
    kb.sources = (kb.sources or []) + sources
    kb.last_refreshed_timestamp = now_ms()
    session.add_all(blobs)
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
    await session.execute(
        sa_delete(KnowledgeBaseFile).where(KnowledgeBaseFile.source_id == source_id)
    )
    await session.commit()


@router.get("/get-knowledge-base-file/{knowledge_base_id}/source/{source_id}")
async def get_knowledge_base_file(
    knowledge_base_id: str,
    source_id: str,
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
):
    row = await session.get(KnowledgeBaseFile, source_id)
    if (
        row is None
        or row.workspace_id != api_key.workspace_id
        or row.knowledge_base_id != knowledge_base_id
    ):
        raise HTTPException(404, detail="File not found")
    return Response(
        content=row.data,
        media_type=row.content_type,
        # RFC 5987 encoding — filenames are user input and may contain
        # quotes/unicode that would break a bare filename= header.
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.filename)}"},
    )
