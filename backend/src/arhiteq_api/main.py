import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import inspect, text

from .api import (
    agent_tests,
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
    workspaces,
)
from .config import get_settings
from .db import get_engine, session_factory
from .models import Base
from .security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
)
from .services import simulation

logging.basicConfig(level=logging.INFO)


# (table, column, DDL type) for columns added after their table shipped.
# Append here when a model grows a column; the loop below does the rest.
_COLUMN_BACKFILLS: tuple[tuple[str, str, str], ...] = (
    ("agents", "folder_id", "VARCHAR(64)"),
    ("agents", "webhook_timeout_ms", "BIGINT"),
    ("agents", "webhook_events", "JSON"),
    ("agents", "pii_config", "JSON"),
    ("agents", "fallback_voice_ids", "JSON"),
    ("agents", "allow_user_dtmf", "BOOLEAN DEFAULT TRUE"),
    ("agents", "allow_dtmf_interruption", "BOOLEAN DEFAULT FALSE"),
    ("agents", "user_dtmf_options", "JSON"),
    ("agents", "opt_in_signed_url", "BOOLEAN DEFAULT FALSE"),
    ("agents", "ivr_option", "JSON"),
    ("agents", "call_screening_option", "JSON"),
    ("agents", "timezone", "VARCHAR(64)"),
    ("agents", "published_version", "INTEGER"),
    ("retell_llms", "mcps", "JSON"),
    ("calls", "collected_dynamic_variables", "JSON"),
    ("contacts", "timezone", "VARCHAR(64)"),
    ("contacts", "custom_fields", "JSON"),
    ("phone_numbers", "fallback_number", "VARCHAR(20)"),
    ("workspaces", "settings", "JSON"),
    ("alerts", "compare_to", "VARCHAR(16) DEFAULT 'value'"),
    ("batch_calls", "reserved_concurrency", "INTEGER"),
    ("batch_calls", "call_time_window", "JSON"),
    ("qa_cohorts", "min_duration_s", "INTEGER"),
    ("qa_cohorts", "success_criteria", "TEXT"),
    ("qa_cohorts", "scoring_metric", "VARCHAR(32) DEFAULT 'call_successful'"),
)

# Indexes on backfilled columns. CREATE INDEX IF NOT EXISTS is idempotent on
# its own, so these run unconditionally.
_BACKFILL_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_agents_folder_id ON agents (folder_id)",
)


def _apply_column_backfills(sync_conn) -> None:
    """Additive schema fixups for columns added after a table shipped.

    create_all only creates missing *tables*, so pre-existing databases need
    new columns added by hand. Everything here must be idempotent.
    """
    # IF NOT EXISTS guards concurrent replica boots racing past the inspect()
    # check (Postgres only; SQLite dev/test DBs are single-process and get the
    # column from create_all anyway).
    guard = "IF NOT EXISTS " if sync_conn.dialect.name == "postgresql" else ""
    inspector = inspect(sync_conn)
    existing: dict[str, set[str]] = {}

    for table, column, ddl in _COLUMN_BACKFILLS:
        columns = existing.setdefault(table, {c["name"] for c in inspector.get_columns(table)})
        if column not in columns:
            sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {guard}{column} {ddl}"))

    for statement in _BACKFILL_INDEXES:
        sync_conn.execute(text(statement))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience; production schema is managed the same way (additive
    # create_all + column backfills on boot).
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_column_backfills)
    yield
    # Simulation batches run detached from any request; cancel them (and mark
    # their rows finished, so the dashboard stops polling) before the engine
    # goes away and a run faults on a disposed connection pool.
    await simulation.shutdown(session_factory())
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
app.include_router(agent_tests.router)
app.include_router(workspaces.router)

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
