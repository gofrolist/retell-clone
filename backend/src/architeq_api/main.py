import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

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
from .security import RateLimitMiddleware, SecurityHeadersMiddleware

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience; production schema is managed by alembic.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Architeq API", version="0.1.0", lifespan=lifespan)
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
