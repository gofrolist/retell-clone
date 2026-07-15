# Knowledge Base: Retell-style UI + real file upload — design

Date: 2026-07-15
Status: approved

## Goal

Make the dashboard Knowledge Base experience match Retell's (see Retell's
"Add Knowledge Base" modal) and make **Upload Files** actually work
end-to-end: file bytes persisted in Postgres, listed with real sizes, and
re-downloadable — like Retell's file rows with download icons.

Out of scope: web crawling/sync for "Add Web Pages" (the entry only records
URL sources, as today), retrieval/embedding (still TODO in the worker), GCS
storage.

## Current state

- Frontend create modal: Name + URLs textarea + Pasted text textarea.
  Detail view has a tabbed URL/Text "Add source" modal. No file upload.
- Backend `/create-knowledge-base` and `/add-knowledge-base-sources` already
  parse multipart `knowledge_base_files` but persist only the filename
  (`api/knowledge_bases.py` `_build_sources` TODO); content is discarded.
- Schema is managed by `Base.metadata.create_all` on boot (new tables appear
  automatically; only new columns on existing tables need backfills).
- Contract shape (frozen): document sources serialize as
  `{"type": "document", "source_id": "src_…", "filename": …}` inside
  `knowledge_base_sources`. Extra fields are allowed; renames/drops are not.

## Design

### 1. Backend — persistence & download

- New model `KnowledgeBaseFile` in `models.py`:
  - `source_id` (PK, the `src_…` id of the document source)
  - `knowledge_base_id` (FK to knowledge_bases, indexed)
  - `workspace_id`
  - `filename`, `content_type`, `size_bytes`
  - `data` (LargeBinary)
  Blobs live outside the `sources` JSON column so list/get endpoints never
  load file bytes.
- `api/knowledge_bases.py`:
  - `_parse_body` keeps the raw `UploadFile`s (content + metadata) instead of
    just filenames.
  - File size cap **20MB per file**; oversized upload → HTTP 413.
  - `_build_sources` creates document sources as
    `{"type": "document", "source_id", "filename", "file_size", "file_url"}`
    and stages a `KnowledgeBaseFile` row per file. `file_size` = bytes,
    `file_url` = absolute URL of the download endpoint (additive fields —
    contract-safe).
  - New endpoint `GET /get-knowledge-base-file/{knowledge_base_id}/source/{source_id}`
    — API-key auth, workspace-scoped 404 like the other KB endpoints;
    responds with the stored bytes, original `content_type`, and
    `Content-Disposition: attachment; filename=…`.
  - `delete-knowledge-base-source` deletes the matching `KnowledgeBaseFile`
    row; `delete-knowledge-base` deletes all rows for the KB.

### 2. Frontend — shared AddSourceMenu

New `frontend/src/components/kb/AddSourceMenu.tsx`: dropdown anchored to a
"+ Add" button, Retell-style entries:

1. **Add Web Pages** — Link icon, sublabel "Crawl and sync your website" →
   URL entry panel (one URL per line).
2. **Upload Files** — Upload icon, sublabel "File size should be less than
   20MB" → opens hidden `<input type="file" multiple
   accept=".pdf,.doc,.docx,.txt,.md,.html,.csv">`.
3. **Add Text** — FileText icon, sublabel "Add articles manually" →
   title + text fields.

The menu emits normalized pending sources
(`{kind: "url"|"file"|"text", …}`) to its parent; it does not call the API
itself, so both consumers below can reuse it.

### 3. Frontend — "Add Knowledge Base" modal (`knowledge-base/page.tsx`)

- Title becomes "Add Knowledge Base"; fields: **Knowledge Base Name** +
  **Documents** section containing the AddSourceMenu.
- Added items render as a removable pending list (type icon, name, size for
  files) inside the modal before saving.
- Footer: Cancel / **Save** (disabled without a name).
- Save posts once: multipart `FormData` when files are present
  (`knowledge_base_name`, repeated `knowledge_base_urls`,
  `knowledge_base_texts` as JSON strings, repeated
  `knowledge_base_files`), plain JSON otherwise.

### 4. Frontend — detail view (`KbDetail.tsx`)

- "Add source" button becomes the AddSourceMenu; each completed entry posts
  immediately to `/add-knowledge-base-sources` (multipart for files).
- Source rows: extension-derived type badge (pdf/md/txt/docx/url/…), real
  `file_size` KB, **download icon** on document sources; delete icon stays
  on all rows.
- Auth is a Bearer header, so a bare `<a href>` can't download: the icon
  calls a `downloadKnowledgeBaseFile` helper that fetches the endpoint with
  the auth header, wraps the response in a blob object URL, and triggers a
  client-side download with the original filename. (`file_url` remains
  useful for API-key consumers, e.g. curl.)

### 5. API client (`lib/api.ts`)

- `createKnowledgeBase` / `addKnowledgeBaseSources` accept optional `File[]`
  and switch to `FormData` when present (browser sets the multipart
  boundary; no explicit content-type).
- `uiKbFromRaw` / `docsFromRawKb` map `file_size` and `file_url` through to
  `KnowledgeDocument` so sizes and download links render.

### 6. Error handling

- Oversized file: backend 413; frontend surfaces the message inline in the
  modal (and pre-checks size client-side to fail fast).
- Download of a missing/foreign blob: 404, same workspace-scoping as other
  KB endpoints.
- Multipart parse failures keep the existing 422 behavior.

### 7. Testing

- Contract tests (`backend/tests/contract/test_knowledge_base.py`):
  - multipart create/add persists content — download endpoint returns the
    exact uploaded bytes with correct headers;
  - document sources include `file_size`/`file_url`;
  - >20MB upload → 413;
  - cross-workspace download → 404;
  - deleting a source / a KB removes its blob rows.
- Frontend: `bun run build`; manual verification via the local stack
  (`/verify` skill): create KB with a file, see it listed with size, download
  it, add/delete sources.
