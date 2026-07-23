import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import inspect, text

from .api import (
    agents,
    auth_google,
    batch_calls,
    calls,
    chat_agents,
    chats,
    concurrency,
    conversation_flows,
    dashboard,
    internal,
    knowledge_bases,
    llms,
    phone_numbers,
    voices,
)
from .config import get_settings
from .db import get_engine
from .models import Base
from .security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)

logging.basicConfig(level=logging.INFO)


def _apply_column_backfills(sync_conn) -> None:
    """Additive schema fixups for columns added after a table shipped.

    create_all only creates missing *tables*, so pre-existing databases need
    new columns added by hand. Everything here must be idempotent.
    """
    guard = "IF NOT EXISTS " if sync_conn.dialect.name == "postgresql" else ""

    agent_cols = {c["name"] for c in inspect(sync_conn).get_columns("agents")}
    if "folder_id" not in agent_cols:
        # IF NOT EXISTS guards concurrent replica boots racing past the
        # inspect() check (Postgres only; SQLite dev/test DBs are
        # single-process and get the column from create_all anyway).
        sync_conn.execute(text(f"ALTER TABLE agents ADD COLUMN {guard}folder_id VARCHAR(64)"))
        sync_conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_agents_folder_id ON agents (folder_id)")
        )
    if "webhook_timeout_ms" not in agent_cols:
        sync_conn.execute(text(f"ALTER TABLE agents ADD COLUMN {guard}webhook_timeout_ms BIGINT"))
    if "webhook_events" not in agent_cols:
        sync_conn.execute(text(f"ALTER TABLE agents ADD COLUMN {guard}webhook_events JSON"))

    call_cols = {c["name"] for c in inspect(sync_conn).get_columns("calls")}
    if "collected_dynamic_variables" not in call_cols:
        sync_conn.execute(
            text(f"ALTER TABLE calls ADD COLUMN {guard}collected_dynamic_variables JSON")
        )

    contact_cols = {c["name"] for c in inspect(sync_conn).get_columns("contacts")}
    if "timezone" not in contact_cols:
        sync_conn.execute(text(f"ALTER TABLE contacts ADD COLUMN {guard}timezone VARCHAR(64)"))

    workspace_cols = {c["name"] for c in inspect(sync_conn).get_columns("workspaces")}
    if "settings" not in workspace_cols:
        sync_conn.execute(text(f"ALTER TABLE workspaces ADD COLUMN {guard}settings JSON"))

    alert_cols = {c["name"] for c in inspect(sync_conn).get_columns("alerts")}
    if "compare_to" not in alert_cols:
        sync_conn.execute(
            text(f"ALTER TABLE alerts ADD COLUMN {guard}compare_to VARCHAR(16) DEFAULT 'value'")
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience; production schema is managed the same way (additive
    # create_all + column backfills on boot).
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_column_backfills)
    yield
    await engine.dispose()


app = FastAPI(title="Arhiteq API", version="0.1.0", lifespan=lifespan)
# Innermost app middleware: catches unhandled exceptions and returns a JSON 500.
# Added first so it sits *inside* CORS below — otherwise error responses would
# skip the CORS layer and reach the browser without Access-Control-Allow-Origin.
app.add_middleware(UnhandledErrorMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)
# Browser-facing CORS is an allowlist (the dashboard origin). Server-to-server
# consumers (Supabase edge functions) don't use CORS at all.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_google.router)
app.include_router(calls.router)
app.include_router(agents.router)
app.include_router(llms.router)
app.include_router(phone_numbers.router)
app.include_router(internal.router)
app.include_router(batch_calls.router)
app.include_router(voices.router)
app.include_router(knowledge_bases.router)
app.include_router(concurrency.router)
app.include_router(conversation_flows.router)
app.include_router(chats.router)
app.include_router(chat_agents.router)
app.include_router(dashboard.router)

# Public, read-only assets (voice preview mp3s). No auth: previews must be
# playable from a bare <audio> tag in the dashboard.
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
