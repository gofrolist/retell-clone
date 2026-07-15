# Retell-style KB UI + File Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Match Retell's Knowledge Base UI (Add Knowledge Base modal with a Documents "+ Add" menu: Add Web Pages / Upload Files / Add Text) and make file upload real: bytes persisted in Postgres, downloadable from the dashboard.

**Architecture:** New `KnowledgeBaseFile` table stores blobs keyed by `source_id`, outside the `sources` JSON so listing never loads bytes. Document sources gain additive `file_size`/`file_url` fields (frozen Retell shape `{type, source_id, filename}` untouched). Frontend gets one shared `AddSourceMenu` dropdown used by both the rewritten create modal (pending list → one multipart POST) and the detail view (immediate posts).

**Tech Stack:** FastAPI + SQLAlchemy async (backend, Python 3.14, uv), Next.js + React 19 + Tailwind (frontend, bun), lucide-react icons.

**Spec:** `docs/superpowers/specs/2026-07-15-kb-retell-ui-design.md`

## Global Constraints

- Wire contract is frozen: never rename/drop fields in `knowledge_base_sources` items (`type`, `source_id`, `filename`, `title`, `url`, `content`). New fields (`file_size`, `file_url`) are additive only.
- File size cap: **20MB per file** (`MAX_FILE_BYTES = 20 * 1024 * 1024`), HTTP 413 when exceeded.
- Backend tests: `cd backend && uv run pytest` (first time: `uv sync`). Schema is `create_all`-managed — a new table needs no migration.
- Frontend: `cd frontend && bun run build` to type-check; `bun run lint` for eslint. No unit-test infra exists — don't add one.
- Frontend files use `@/*` → `./src/*` imports; this Next.js version has breaking changes — check `frontend/node_modules/next/dist/docs/` if a Next API misbehaves.
- Commit messages: conventional commits; pre-commit hooks (gitleaks, ruff, pytest, eslint) run on commit. Work on branch `feat/kb-retell-ui`.
- Every commit ends with the `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` trailer (as in prior commits on this branch).

---

### Task 1: Backend — persist uploaded file blobs + download endpoint

**Files:**
- Modify: `backend/src/architeq_api/models.py` (add `KnowledgeBaseFile` after `KnowledgeBase`, ~line 319; add `LargeBinary` import)
- Modify: `backend/src/architeq_api/api/knowledge_bases.py` (whole flow: `_parse_body`, `_build_sources`, create/add handlers, new download route)
- Test: `backend/tests/contract/test_knowledge_base.py`

**Interfaces:**
- Consumes: existing `KnowledgeBase` model, `new_source_id()` / `new_knowledge_base_id()` from `ids.py`, `get_settings().public_api_url` (empty string → relative URLs), `knowledge_base_to_dict` (unchanged — sources are stored verbatim).
- Produces: model `KnowledgeBaseFile(source_id: str PK, knowledge_base_id: str FK indexed, workspace_id: str FK indexed, filename: str, content_type: str, size_bytes: int, data: bytes, created_at_ms: int)`; document sources shaped `{"type": "document", "source_id", "filename", "file_size": int, "file_url": str}`; route `GET /get-knowledge-base-file/{knowledge_base_id}/source/{source_id}` → file bytes with `Content-Disposition: attachment`. Task 2 relies on `MAX_FILE_BYTES` module constant and `KnowledgeBaseFile` import; Task 3+ rely on the JSON shape and route path.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/contract/test_knowledge_base.py`:

```python
async def test_uploaded_file_roundtrip(client):
    payload = b"%PDF-1.4 round trip content"
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Files KB"},
        files={"knowledge_base_files": ("guide.pdf", payload, "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")
    assert doc["filename"] == "guide.pdf"
    assert doc["file_size"] == len(payload)
    assert doc["file_url"].endswith(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}"
    )

    dl = await client.get(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert dl.status_code == 200
    assert dl.content == payload
    assert dl.headers["content-type"].startswith("application/pdf")
    assert "attachment" in dl.headers["content-disposition"]
    assert "guide.pdf" in dl.headers["content-disposition"]


async def test_add_sources_uploads_file(client):
    kb = await _create_kb(client)
    payload = b"# notes\nadded later"
    resp = await client.post(
        f"/add-knowledge-base-sources/{kb['knowledge_base_id']}",
        headers=AUTH_HEADERS,
        files={"knowledge_base_files": ("notes.md", payload, "text/markdown")},
    )
    assert resp.status_code == 201
    doc = next(s for s in resp.json()["knowledge_base_sources"] if s["type"] == "document")
    assert doc["file_size"] == len(payload)

    dl = await client.get(
        f"/get-knowledge-base-file/{kb['knowledge_base_id']}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert dl.status_code == 200
    assert dl.content == payload


async def test_download_missing_file_404(client):
    kb = await _create_kb(client)
    resp = await client.get(
        f"/get-knowledge-base-file/{kb['knowledge_base_id']}/source/src_missing",
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/contract/test_knowledge_base.py -v`
Expected: the three new tests FAIL (`KeyError: 'file_size'` and 404/405 on the download route); all pre-existing tests PASS.

- [ ] **Step 3: Add the `KnowledgeBaseFile` model**

In `backend/src/architeq_api/models.py`, add `LargeBinary` to the existing `from sqlalchemy import (...)` block (alphabetical order, after `JSON`/`Integer`), then add after the `KnowledgeBase` class (~line 319):

```python
class KnowledgeBaseFile(Base):
    """Uploaded document blobs, kept out of KnowledgeBase.sources JSON so
    list/get endpoints never load file bytes."""

    __tablename__ = "knowledge_base_files"

    source_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    knowledge_base_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_bases.knowledge_base_id"), index=True
    )
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at_ms: Mapped[int] = mapped_column(BigInteger, default=now_ms)
```

- [ ] **Step 4: Rework `api/knowledge_bases.py` to persist blobs and serve downloads**

Replace the imports/header, `_parse_body`, `_build_sources`, `create_knowledge_base`, and `add_knowledge_base_sources`, and add the download route. Full new content for those parts (leave `_get_workspace_kb`, `get`, `list`, `delete` handlers as they are):

```python
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
from sqlalchemy import select
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
            sources.append(
                {"type": "document", "source_id": new_source_id(), "filename": file}
            )
            continue
        content_bytes = await file.read()
        source_id = new_source_id()
        filename = file.filename or source_id
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
                content_type=file.content_type or "application/octet-stream",
                size_bytes=len(content_bytes),
                data=content_bytes,
            )
        )
    return sources, blobs
```

Updated create handler (generates the KB id up-front so `file_url` can reference it):

```python
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
```

Updated add-sources handler:

```python
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
```

New download route (append at the end of the file):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/contract/test_knowledge_base.py -v`
Expected: ALL tests PASS (including the pre-existing multipart test — its fake PDF now round-trips into a blob row).

- [ ] **Step 6: Run the full backend suite**

Run: `cd backend && uv run pytest`
Expected: PASS (no other module touches `_build_sources`).

- [ ] **Step 7: Commit**

```bash
git add backend/src/architeq_api/models.py backend/src/architeq_api/api/knowledge_bases.py backend/tests/contract/test_knowledge_base.py
git commit -m "feat: persist knowledge base file uploads and serve downloads"
```

---

### Task 2: Backend — 20MB cap, blob cleanup on delete, workspace scoping

**Files:**
- Modify: `backend/src/architeq_api/api/knowledge_bases.py`
- Test: `backend/tests/contract/test_knowledge_base.py`

**Interfaces:**
- Consumes: Task 1's `MAX_FILE_BYTES`, `KnowledgeBaseFile`, download route.
- Produces: 413 on oversized upload; deleting a source or KB removes its blob rows; cross-workspace download → 404. No new names for later tasks.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/contract/test_knowledge_base.py` (note the module import for monkeypatching):

```python
from architeq_api.api import knowledge_bases as kb_module


async def test_upload_over_size_cap_413(client, monkeypatch):
    monkeypatch.setattr(kb_module, "MAX_FILE_BYTES", 10)
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Too big"},
        files={"knowledge_base_files": ("big.pdf", b"x" * 11, "application/pdf")},
    )
    assert resp.status_code == 413


async def test_download_scoped_to_workspace(client, other_workspace):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Scoped"},
        files={"knowledge_base_files": ("a.txt", b"secret", "text/plain")},
    )
    body = resp.json()
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")
    dl = await client.get(
        f"/get-knowledge-base-file/{body['knowledge_base_id']}/source/{doc['source_id']}",
        headers=OTHER_AUTH_HEADERS,
    )
    assert dl.status_code == 404


async def test_delete_source_removes_blob(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Cleanup"},
        files={"knowledge_base_files": ("a.txt", b"bye", "text/plain")},
    )
    body = resp.json()
    kb_id = body["knowledge_base_id"]
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")

    deleted = await client.delete(
        f"/delete-knowledge-base-source/{kb_id}/source/{doc['source_id']}",
        headers=AUTH_HEADERS,
    )
    assert deleted.status_code == 204
    dl = await client.get(
        f"/get-knowledge-base-file/{kb_id}/source/{doc['source_id']}", headers=AUTH_HEADERS
    )
    assert dl.status_code == 404


async def test_delete_kb_removes_blobs(client):
    resp = await client.post(
        "/create-knowledge-base",
        headers=AUTH_HEADERS,
        data={"knowledge_base_name": "Cleanup KB"},
        files={"knowledge_base_files": ("a.txt", b"bye", "text/plain")},
    )
    body = resp.json()
    kb_id = body["knowledge_base_id"]
    doc = next(s for s in body["knowledge_base_sources"] if s["type"] == "document")

    await client.delete(f"/delete-knowledge-base/{kb_id}", headers=AUTH_HEADERS)
    # The download route checks the blob row directly, so a 404 here proves
    # the row went away with the KB (not merely that the KB is gone).
    dl = await client.get(
        f"/get-knowledge-base-file/{kb_id}/source/{doc['source_id']}", headers=AUTH_HEADERS
    )
    assert dl.status_code == 404
```

Place the `from architeq_api.api import knowledge_bases as kb_module` import at the top of the file with the other imports.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/contract/test_knowledge_base.py -v`
Expected: `test_upload_over_size_cap_413` FAILS (201, no cap yet), `test_delete_source_removes_blob` and `test_delete_kb_removes_blobs` FAIL (200 — blob rows survive). `test_download_scoped_to_workspace` already PASSES (Task 1 checks workspace) — keep it as a regression guard.

- [ ] **Step 3: Implement cap + cleanup**

In `api/knowledge_bases.py`:

1. Add `delete` to the sqlalchemy import: `from sqlalchemy import delete as sa_delete, select`
2. In `_build_sources`, right after `content_bytes = await file.read()`:

```python
        if len(content_bytes) > MAX_FILE_BYTES:
            raise HTTPException(413, detail="File exceeds the 20MB limit")
```

3. In `delete_knowledge_base`, before `await session.delete(kb)`:

```python
    await session.execute(
        sa_delete(KnowledgeBaseFile).where(
            KnowledgeBaseFile.knowledge_base_id == kb.knowledge_base_id
        )
    )
```

4. In `delete_knowledge_base_source`, after the `kb.sources = remaining` line (before commit):

```python
    await session.execute(
        sa_delete(KnowledgeBaseFile).where(KnowledgeBaseFile.source_id == source_id)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/contract/test_knowledge_base.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Run the full backend suite and commit**

Run: `cd backend && uv run pytest`
Expected: PASS.

```bash
git add backend/src/architeq_api/api/knowledge_bases.py backend/tests/contract/test_knowledge_base.py
git commit -m "feat: enforce 20MB KB upload cap and clean up file blobs on delete"
```

---

### Task 3: Frontend lib — multipart API client, doc mapping, download helper

**Files:**
- Modify: `frontend/src/lib/types.ts` (KnowledgeDocument, ~line 106)
- Modify: `frontend/src/lib/api.ts` (request(), RawKnowledgeBase, uiKbFromRaw, KB api methods)
- Modify: `frontend/src/components/kb/KbDetail.tsx` (delete local `docsFromRawKb`, import from lib)
- Modify: `frontend/src/app/(shell)/knowledge-base/page.tsx` (import `docsFromRawKb` from `@/lib/api` instead of KbDetail)

**Interfaces:**
- Consumes: backend shapes from Tasks 1–2 (`file_size`, `file_url`, download route).
- Produces (used by Tasks 4–6):
  - `KnowledgeDocument` gains `file_url?: string`; `type` widens from a union to `string` (extension-derived).
  - `export function docsFromRawKb(raw: RawKnowledgeBase): KnowledgeDocument[]` now lives in `lib/api.ts`.
  - `api.createKnowledgeBase(body: { knowledge_base_name: string; knowledge_base_urls?: string[]; knowledge_base_texts?: { title: string; text: string }[] }, files?: File[])`
  - `api.addKnowledgeBaseSources(id: string, body: { knowledge_base_urls?: string[]; knowledge_base_texts?: { title: string; text: string }[] }, files?: File[])`
  - `api.downloadKnowledgeBaseFile(id: string, sourceId: string): Promise<Blob>`

- [ ] **Step 1: Update `KnowledgeDocument` in `types.ts`**

```ts
export interface KnowledgeDocument {
  document_id: string;
  name: string;
  /** Badge label: file extension for documents ("pdf", "md", …), "url", or "txt". */
  type: string;
  size_kb: number;
  /** Present on uploaded files; download goes through api.downloadKnowledgeBaseFile. */
  file_url?: string;
}
```

- [ ] **Step 2: Update `api.ts`**

1. `request()` — don't force the JSON content-type onto FormData bodies (the browser must set the multipart boundary). Replace the headers block:

```ts
      headers: {
        ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
```

2. `RawKnowledgeBase.knowledge_base_sources` items gain the new fields:

```ts
  knowledge_base_sources: {
    source_id: string;
    type: string;
    title?: string;
    url?: string;
    content?: string;
    filename?: string;
    file_size?: number;
    file_url?: string;
  }[];
```

3. Shared source→document mapper next to `uiKbFromRaw` (this replaces both the inline map in `uiKbFromRaw` and KbDetail's local `docsFromRawKb`):

```ts
function extFromFilename(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot + 1).toLowerCase() : "txt";
}

function kbDocFromSource(s: RawKnowledgeBase["knowledge_base_sources"][number]): KnowledgeDocument {
  return {
    document_id: s.source_id,
    name: s.title ?? s.url ?? s.filename ?? s.source_id,
    type:
      s.type === "url" ? "url" : s.type === "document" ? extFromFilename(s.filename ?? "") : "txt",
    size_kb: s.file_size
      ? Math.max(1, Math.round(s.file_size / 1024))
      : s.content
        ? Math.max(1, Math.round(s.content.length / 1024))
        : 0,
    file_url: s.file_url,
  };
}

export function docsFromRawKb(raw: RawKnowledgeBase): KnowledgeDocument[] {
  return (raw.knowledge_base_sources ?? []).map(kbDocFromSource);
}
```

and `uiKbFromRaw` uses it:

```ts
export function uiKbFromRaw(k: RawKnowledgeBase): KnowledgeBase {
  return {
    knowledge_base_id: k.knowledge_base_id,
    knowledge_base_name: k.knowledge_base_name,
    status: k.status === "complete" ? "ready" : "processing",
    uploaded_by: k.last_refreshed_timestamp
      ? new Date(k.last_refreshed_timestamp).toLocaleDateString()
      : "",
    documents: docsFromRawKb(k),
  };
}
```

4. Replace the KB api methods (keep `listKnowledgeBases`, `deleteKnowledgeBase`, `deleteKnowledgeBaseSource` as-is):

```ts
  createKnowledgeBase: (
    body: {
      knowledge_base_name: string;
      knowledge_base_texts?: { title: string; text: string }[];
      knowledge_base_urls?: string[];
    },
    files: File[] = [],
  ) =>
    files.length
      ? request<RawKnowledgeBase>("/create-knowledge-base", {
          method: "POST",
          body: kbFormData(body, files),
        })
      : request<RawKnowledgeBase>("/create-knowledge-base", post(body)),

  addKnowledgeBaseSources: (
    id: string,
    body: {
      knowledge_base_texts?: { title: string; text: string }[];
      knowledge_base_urls?: string[];
    },
    files: File[] = [],
  ) =>
    files.length
      ? request<RawKnowledgeBase>(`/add-knowledge-base-sources/${encodeURIComponent(id)}`, {
          method: "POST",
          body: kbFormData(body, files),
        })
      : request<RawKnowledgeBase>(`/add-knowledge-base-sources/${encodeURIComponent(id)}`, post(body)),

  downloadKnowledgeBaseFile: async (id: string, sourceId: string): Promise<Blob> => {
    const token = bearerToken();
    const res = await fetch(
      `${API_BASE}/get-knowledge-base-file/${encodeURIComponent(id)}/source/${encodeURIComponent(sourceId)}`,
      {
        cache: "no-store",
        signal: AbortSignal.timeout(30_000),
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      },
    );
    if (!res.ok) throw new ApiError(`Download failed (${res.status})`, res.status);
    return res.blob();
  },
```

with the helper placed near `post`/`del` (~line 121):

```ts
/** Retell's multipart shape: repeated fields, texts as JSON strings. */
function kbFormData(
  fields: {
    knowledge_base_name?: string;
    knowledge_base_urls?: string[];
    knowledge_base_texts?: { title: string; text: string }[];
  },
  files: File[],
): FormData {
  const fd = new FormData();
  if (fields.knowledge_base_name) fd.append("knowledge_base_name", fields.knowledge_base_name);
  for (const url of fields.knowledge_base_urls ?? []) fd.append("knowledge_base_urls", url);
  for (const t of fields.knowledge_base_texts ?? [])
    fd.append("knowledge_base_texts", JSON.stringify(t));
  for (const f of files) fd.append("knowledge_base_files", f, f.name);
  return fd;
}
```

- [ ] **Step 3: Repoint the two `docsFromRawKb` consumers**

- `KbDetail.tsx`: delete the local `docsFromRawKb` (lines 21–46) and its now-unused imports if any; add `docsFromRawKb` to the existing `@/lib/api` import.
- `knowledge-base/page.tsx`: change `import KbDetail, { docsFromRawKb } from "@/components/kb/KbDetail";` to `import KbDetail from "@/components/kb/KbDetail";` and add `docsFromRawKb` to the `@/lib/api` import.

- [ ] **Step 4: Verify with build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: build succeeds, no type errors, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/kb/KbDetail.tsx "frontend/src/app/(shell)/knowledge-base/page.tsx"
git commit -m "feat: multipart KB uploads and file downloads in the dashboard api client"
```

---

### Task 4: Frontend — shared AddSourceMenu component

**Files:**
- Create: `frontend/src/components/kb/AddSourceMenu.tsx`

**Interfaces:**
- Consumes: `Modal`, `Button`, `Field`/`TextInput` from `@/components/ui`, `useClickOutside` from `@/lib/useClickOutside`, lucide icons.
- Produces (used by Tasks 5–6):

```ts
export type PendingSource =
  | { kind: "url"; url: string }
  | { kind: "text"; title: string; text: string }
  | { kind: "file"; file: File };

export const MAX_FILE_MB = 20;

export default function AddSourceMenu(props: {
  onAdd: (sources: PendingSource[]) => void;
  onError?: (message: string) => void;
  label?: string; // button text, default "Add"
}): JSX.Element;
```

- [ ] **Step 1: Create the component**

`frontend/src/components/kb/AddSourceMenu.tsx`:

```tsx
"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { useClickOutside } from "@/lib/useClickOutside";
import { FileText, Link2, Plus, Upload } from "lucide-react";
import { useCallback, useRef, useState } from "react";

export type PendingSource =
  | { kind: "url"; url: string }
  | { kind: "text"; title: string; text: string }
  | { kind: "file"; file: File };

export const MAX_FILE_MB = 20;
const ACCEPT = ".pdf,.doc,.docx,.txt,.md,.html,.csv";

const MENU_ITEMS = [
  {
    key: "url" as const,
    icon: Link2,
    title: "Add Web Pages",
    subtitle: "Crawl and sync your website",
  },
  {
    key: "file" as const,
    icon: Upload,
    title: "Upload Files",
    subtitle: `File size should be less than ${MAX_FILE_MB}MB`,
  },
  {
    key: "text" as const,
    icon: FileText,
    title: "Add Text",
    subtitle: "Add articles manually",
  },
];

/**
 * Retell-style "+ Add" source menu. Emits normalized PendingSources and
 * never calls the API itself, so the create modal can batch them while the
 * detail view posts immediately.
 */
export default function AddSourceMenu({
  onAdd,
  onError,
  label = "Add",
}: {
  onAdd: (sources: PendingSource[]) => void;
  onError?: (message: string) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<"url" | "text" | null>(null);
  const [urlsText, setUrlsText] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [text, setText] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useClickOutside(
    menuRef,
    useCallback(() => setOpen(false), []),
  );

  function pick(key: "url" | "file" | "text") {
    setOpen(false);
    if (key === "file") fileRef.current?.click();
    else setPanel(key);
  }

  function onFiles(list: FileList | null) {
    if (!list?.length) return;
    const files = Array.from(list);
    const oversized = files.filter((f) => f.size > MAX_FILE_MB * 1024 * 1024);
    if (oversized.length) {
      onError?.(
        `${oversized.map((f) => f.name).join(", ")} exceed${oversized.length === 1 ? "s" : ""} the ${MAX_FILE_MB}MB limit.`,
      );
    }
    const ok = files.filter((f) => f.size <= MAX_FILE_MB * 1024 * 1024);
    if (ok.length) onAdd(ok.map((file) => ({ kind: "file" as const, file })));
    if (fileRef.current) fileRef.current.value = "";
  }

  function submitUrls() {
    const urls = urlsText
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);
    if (!urls.length) return;
    onAdd(urls.map((url) => ({ kind: "url" as const, url })));
    setUrlsText("");
    setPanel(null);
  }

  function submitText() {
    if (!textTitle.trim() || !text.trim()) return;
    onAdd([{ kind: "text", title: textTitle.trim(), text: text.trim() }]);
    setTextTitle("");
    setText("");
    setPanel(null);
  }

  return (
    <div ref={menuRef} className="relative inline-block">
      <Button size="sm" onClick={() => setOpen((v) => !v)}>
        <Plus className="size-3.5" /> {label}
      </Button>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => onFiles(e.target.files)}
      />
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-72 rounded-xl border border-line bg-white p-1.5 shadow-lg">
          {MENU_ITEMS.map(({ key, icon: Icon, title, subtitle }) => (
            <button
              key={key}
              onClick={() => pick(key)}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-app cursor-pointer"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-line text-sub">
                <Icon className="size-4" strokeWidth={1.8} />
              </span>
              <span>
                <span className="block text-[13.5px] font-medium">{title}</span>
                <span className="block text-xs text-sub">{subtitle}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      <Modal
        open={panel === "url"}
        onClose={() => setPanel(null)}
        title="Add Web Pages"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPanel(null)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!urlsText.trim()} onClick={submitUrls}>
              Add
            </Button>
          </>
        }
      >
        <Field label="URLs" hint="One URL per line.">
          <textarea
            value={urlsText}
            onChange={(e) => setUrlsText(e.target.value)}
            rows={4}
            placeholder={"https://example.com/docs\nhttps://example.com/faq"}
            autoFocus
            className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
        </Field>
      </Modal>

      <Modal
        open={panel === "text"}
        onClose={() => setPanel(null)}
        title="Add Text"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPanel(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!textTitle.trim() || !text.trim()}
              onClick={submitText}
            >
              Add
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Title">
            <TextInput
              value={textTitle}
              onChange={(e) => setTextTitle(e.target.value)}
              placeholder="e.g. Refund policy"
              autoFocus
            />
          </Field>
          <Field label="Text">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              placeholder="Paste the content here…"
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
            />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 2: Verify with build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: succeeds. (Component not yet mounted anywhere — that's fine; unused-export lint rules aren't configured here.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/kb/AddSourceMenu.tsx
git commit -m "feat: Retell-style AddSourceMenu for knowledge base sources"
```

---

### Task 5: Frontend — "Add Knowledge Base" modal with pending documents

**Files:**
- Modify: `frontend/src/app/(shell)/knowledge-base/page.tsx`

**Interfaces:**
- Consumes: `AddSourceMenu` + `PendingSource` (Task 4), `api.createKnowledgeBase(body, files)` and `docsFromRawKb` (Task 3).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Rewrite the create-modal portion of the page**

In `page.tsx`, replace the `urlsText`/`pastedText` state with a pending-source list, and the modal body with the Retell layout. New imports:

```tsx
import AddSourceMenu, { type PendingSource } from "@/components/kb/AddSourceMenu";
import { api, docsFromRawKb } from "@/lib/api";
import { FileText, Library, Link2, Plus, X } from "lucide-react";
```

State (replaces `urlsText` and `pastedText`):

```tsx
  const [pending, setPending] = useState<PendingSource[]>([]);
```

New `create()`:

```tsx
  async function create() {
    const kbName = name.trim();
    if (!kbName) {
      setCreateError("Name is required.");
      return;
    }
    const urls = pending.flatMap((p) => (p.kind === "url" ? [p.url] : []));
    const texts = pending.flatMap((p) =>
      p.kind === "text" ? [{ title: p.title, text: p.text }] : [],
    );
    const files = pending.flatMap((p) => (p.kind === "file" ? [p.file] : []));
    setCreating(true);
    setCreateError(null);
    try {
      const raw = await api.createKnowledgeBase(
        {
          knowledge_base_name: kbName,
          ...(urls.length ? { knowledge_base_urls: urls } : {}),
          ...(texts.length ? { knowledge_base_texts: texts } : {}),
        },
        files,
      );
      setDocsOverride((m) => ({ ...m, [raw.knowledge_base_id]: docsFromRawKb(raw) }));
      await refresh();
      setSelected(raw.knowledge_base_id);
      setName("");
      setPending([]);
      setCreateOpen(false);
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : "Failed to create knowledge base");
    } finally {
      setCreating(false);
    }
  }
```

Helpers for rendering pending rows (place above the component or inside it):

```tsx
function pendingLabel(p: PendingSource): string {
  if (p.kind === "url") return p.url;
  if (p.kind === "text") return p.title;
  return p.file.name;
}

function pendingMeta(p: PendingSource): string {
  if (p.kind === "url") return "Web page";
  if (p.kind === "text") return "Text";
  return `${Math.max(1, Math.round(p.file.size / 1024))} KB`;
}
```

New modal JSX (replaces the current `<Modal …>` block):

```tsx
      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Add Knowledge Base"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={creating || !name.trim()} onClick={create}>
              {creating ? "Saving…" : "Save"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Knowledge Base Name">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter"
              autoFocus
            />
          </Field>
          <div>
            <div className="mb-1.5 text-[13px] font-medium">Documents</div>
            <AddSourceMenu
              onAdd={(sources) => {
                setPending((prev) => [...prev, ...sources]);
                setCreateError(null);
              }}
              onError={setCreateError}
            />
            {pending.length > 0 && (
              <div className="mt-3 divide-y divide-line rounded-lg border border-line">
                {pending.map((p, i) => (
                  <div key={i} className="flex items-center gap-2.5 px-3 py-2">
                    {p.kind === "url" ? (
                      <Link2 className="size-4 shrink-0 text-sub" strokeWidth={1.8} />
                    ) : (
                      <FileText className="size-4 shrink-0 text-sub" strokeWidth={1.8} />
                    )}
                    <div className="min-w-0 grow">
                      <div className="truncate text-[13px] font-medium">{pendingLabel(p)}</div>
                      <div className="text-xs text-sub">{pendingMeta(p)}</div>
                    </div>
                    <button
                      onClick={() => setPending((prev) => prev.filter((_, j) => j !== i))}
                      className="rounded-md p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
                      aria-label={`Remove ${pendingLabel(p)}`}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          {createError && <p className="text-[13px] text-bad">{createError}</p>}
        </div>
      </Modal>
```

Also reset `pending` when closing the modal is fine to skip (kept for reopening), but clear `createError` on close if it's stale — leave the existing `onClose` as-is.

- [ ] **Step 2: Verify with build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: succeeds; no unused-variable errors (the old `urlsText`/`pastedText` state and their JSX are gone).

- [ ] **Step 3: Commit**

```bash
git add "frontend/src/app/(shell)/knowledge-base/page.tsx"
git commit -m "feat: Retell-style Add Knowledge Base modal with pending documents"
```

---

### Task 6: Frontend — detail view: AddSourceMenu, file badges, download

**Files:**
- Modify: `frontend/src/components/kb/KbDetail.tsx`

**Interfaces:**
- Consumes: `AddSourceMenu`/`PendingSource` (Task 4), `api.addKnowledgeBaseSources(id, body, files)`, `api.downloadKnowledgeBaseFile`, `docsFromRawKb` (Task 3).
- Produces: nothing new for later tasks.

- [ ] **Step 1: Replace the tabbed add-source modal with the menu, add download**

Rewrite `KbDetail.tsx`:

```tsx
"use client";

import AddSourceMenu, { type PendingSource } from "@/components/kb/AddSourceMenu";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { api, docsFromRawKb } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import { truncateId } from "@/lib/utils";
import { CheckCircle2, Download, FileText, Trash2 } from "lucide-react";
import { useState } from "react";

const TYPE_STYLES: Record<string, string> = {
  md: "bg-sky-50 text-sky-700 border-sky-100",
  pdf: "bg-rose-50 text-rose-600 border-rose-100",
  doc: "bg-blue-50 text-blue-700 border-blue-100",
  docx: "bg-blue-50 text-blue-700 border-blue-100",
  csv: "bg-emerald-50 text-emerald-700 border-emerald-100",
  html: "bg-amber-50 text-amber-700 border-amber-100",
  txt: "bg-app text-sub border-line",
  url: "bg-violet-50 text-violet-700 border-violet-100",
};

export default function KbDetail({
  kb,
  onDeleted,
  onSourcesChanged,
}: {
  kb: KnowledgeBase;
  onDeleted: () => void;
  onSourcesChanged: (kbId: string, docs: KnowledgeDocument[]) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  async function removeKb() {
    if (!window.confirm(`Delete knowledge base "${kb.knowledge_base_name}"? This cannot be undone.`))
      return;
    setError(null);
    try {
      await api.deleteKnowledgeBase(kb.knowledge_base_id);
      onDeleted();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete knowledge base");
    }
  }

  async function removeSource(doc: KnowledgeDocument) {
    if (!window.confirm(`Delete source "${doc.name}"?`)) return;
    setError(null);
    try {
      await api.deleteKnowledgeBaseSource(kb.knowledge_base_id, doc.document_id);
      onSourcesChanged(
        kb.knowledge_base_id,
        kb.documents.filter((d) => d.document_id !== doc.document_id),
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete source");
    }
  }

  async function addSources(sources: PendingSource[]) {
    const urls = sources.flatMap((p) => (p.kind === "url" ? [p.url] : []));
    const texts = sources.flatMap((p) =>
      p.kind === "text" ? [{ title: p.title, text: p.text }] : [],
    );
    const files = sources.flatMap((p) => (p.kind === "file" ? [p.file] : []));
    setAdding(true);
    setError(null);
    try {
      const raw = await api.addKnowledgeBaseSources(
        kb.knowledge_base_id,
        {
          ...(urls.length ? { knowledge_base_urls: urls } : {}),
          ...(texts.length ? { knowledge_base_texts: texts } : {}),
        },
        files,
      );
      onSourcesChanged(kb.knowledge_base_id, docsFromRawKb(raw));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add source");
    } finally {
      setAdding(false);
    }
  }

  async function download(doc: KnowledgeDocument) {
    setError(null);
    try {
      const blob = await api.downloadKnowledgeBaseFile(kb.knowledge_base_id, doc.document_id);
      const href = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = doc.name;
      a.click();
      URL.revokeObjectURL(href);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to download file");
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[17px] font-semibold">{kb.knowledge_base_name}</h1>
          <div className="mt-1 flex items-center gap-3 text-[13px] text-sub">
            <CopyId value={kb.knowledge_base_id} display={truncateId(kb.knowledge_base_id, 8)} />
            {kb.uploaded_by && (
              <span className="inline-flex items-center gap-1">
                Last refreshed: {kb.uploaded_by}
                <CheckCircle2 className="size-3.5 text-ok" />
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <AddSourceMenu label={adding ? "Adding…" : "Add source"} onAdd={addSources} onError={setError} />
          <Button size="sm" variant="danger" onClick={removeKb} aria-label="Delete knowledge base">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
          {error}
        </p>
      )}

      {kb.documents.length === 0 ? (
        <div className="mt-5 rounded-xl border border-line bg-white px-4 py-10 text-center text-[13px] text-sub shadow-sm">
          No sources yet. Add web pages, files, or text to ground your agents.
        </div>
      ) : (
        <div className="mt-5 divide-y divide-line rounded-xl border border-line bg-white shadow-sm">
          {kb.documents.map((doc) => (
            <div key={doc.document_id} className="flex items-center gap-3 px-4 py-3">
              <span
                className={`flex size-8 items-center justify-center rounded-lg border text-[10px] font-bold uppercase ${TYPE_STYLES[doc.type] ?? TYPE_STYLES.txt}`}
              >
                {doc.type}
              </span>
              <div className="min-w-0 grow">
                <div className="flex items-center gap-1.5 truncate text-[13.5px] font-medium">
                  <FileText className="size-3.5 text-faint shrink-0" />
                  {doc.name}
                </div>
                <div className="text-xs text-sub">{doc.size_kb} KB</div>
              </div>
              {doc.file_url && (
                <button
                  onClick={() => download(doc)}
                  className="rounded-md p-1.5 text-faint hover:bg-app hover:text-ink cursor-pointer"
                  aria-label={`Download ${doc.name}`}
                >
                  <Download className="size-4" />
                </button>
              )}
              <button
                onClick={() => removeSource(doc)}
                className="rounded-md p-1.5 text-faint hover:bg-red-50 hover:text-bad cursor-pointer"
                aria-label={`Delete ${doc.name}`}
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

Note: the `Modal`, `Field`, `TextInput`, `UnderlineTabs`, `Plus`, and `RawKnowledgeBase` imports from the old version are gone — make sure they're removed so lint passes.

- [ ] **Step 2: Verify with build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/kb/KbDetail.tsx
git commit -m "feat: Retell-style add-source menu and file downloads in KB detail"
```

---

### Task 7: End-to-end verification

**Files:**
- None (verification only; fix regressions in the files above if found).

**Interfaces:**
- Consumes: everything above.
- Produces: a verified branch ready for PR.

- [ ] **Step 1: Full backend suite**

Run: `cd backend && uv run pytest`
Expected: PASS.

- [ ] **Step 2: Frontend build + lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: PASS.

- [ ] **Step 3: Manual smoke test via the local stack**

Use the project's `/verify` skill (docker compose + `make api` + `make web`) and confirm in the dashboard:
1. Knowledge Base → "+" opens **Add Knowledge Base** with Name + Documents "+ Add".
2. "+ Add" menu shows the three Retell-style entries with icons and sublabels.
3. Upload a small `.md` file + add a text article + a URL → pending list shows all three with remove buttons → Save creates the KB and all sources render with correct badges/sizes.
4. Download icon appears only on the uploaded file row and downloads the original bytes.
5. Detail-view "Add source" menu uploads another file immediately; deleting a source removes its row.

- [ ] **Step 4: Pre-commit sweep**

Run: `pre-commit run --all-files`
Expected: all hooks pass (ruff, eslint, pytest, gitleaks).

- [ ] **Step 5: Wrap up**

Use the superpowers:finishing-a-development-branch skill (branch `feat/kb-retell-ui`, PR title `feat: Retell-style knowledge base UI with file upload`).
