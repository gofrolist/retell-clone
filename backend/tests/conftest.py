# ruff: noqa: E402 — env vars must be set before arhiteq_api imports
import atexit
import os
import tempfile

# File-backed SQLite (not :memory:) so every session gets its own connection,
# like production Postgres. In-memory SQLite forces one shared connection
# (StaticPool), where a background task's session close() issues ROLLBACK on
# that connection and can wipe a request's flushed-but-uncommitted UPDATE —
# seen on CI as finalize returning 200 with the transcript silently lost.
_db_file = tempfile.NamedTemporaryFile(prefix="arhiteq-test-", suffix=".sqlite", delete=False)
_db_file.close()
atexit.register(
    lambda: [
        os.path.exists(p) and os.remove(p)
        for p in (_db_file.name, _db_file.name + "-wal", _db_file.name + "-shm")
    ]
)
os.environ["ARHITEQ_DATABASE_URL"] = f"sqlite+aiosqlite:///{_db_file.name}"
os.environ["ARHITEQ_INTERNAL_TOKEN"] = "test-internal-token"
os.environ["ARHITEQ_FUNCTION_SECRET"] = "test-function-secret"
# Webhook targets in tests are fake hosts intercepted by respx — skip the
# SSRF public-address check (its own unit tests exercise it directly).
os.environ["ARHITEQ_ALLOW_PRIVATE_WEBHOOKS"] = "true"
os.environ["ARHITEQ_SESSION_SECRET"] = "test-session-secret"
os.environ["ARHITEQ_DASHBOARD_ALLOWED_EMAILS"] = '["admin@example.com"]'
os.environ["ARHITEQ_RATE_LIMIT_RPM"] = "0"
os.environ.pop("ARHITEQ_GOOGLE_API_KEY", None)

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event

import arhiteq_api.db as db_module
from arhiteq_api.api import batch_calls
from arhiteq_api.auth import hash_key
from arhiteq_api.services import webhooks
from arhiteq_api.main import app
from arhiteq_api.models import Agent, ApiKey, Base, PhoneNumber, RetellLLM, Workspace

API_KEY = "key_test_0123456789abcdef0123456789abcdef"
AGENT_ID = "agent_sales0000000000000000000001"
COMPANION_AGENT_ID = "agent_companion00000000000000001"
LLM_ID = "llm_000000000000000000000000000001"
FROM_NUMBER = "+19499195585"
WORKSPACE_ID = "ws_test000000000000000000"

INTERNAL_HEADERS = {"X-Internal-Token": "test-internal-token"}
AUTH_HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture(autouse=True)
async def _fresh_db():
    # Fresh engine + empty schema per test (the DB file persists across tests).
    db_module._engine = None
    db_module._session_factory = None
    engine = db_module.get_engine()

    @event.listens_for(engine.sync_engine, "connect")
    def _fast_sqlite(dbapi_conn, _record):
        # No durability needed in tests; WAL lets readers and the background
        # webhook/analysis tasks' writers coexist without lock errors.
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA synchronous=OFF")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with db_module.session_factory()() as session:
        session.add(Workspace(id=WORKSPACE_ID, name="Test", webhook_url=None))
        session.add(
            ApiKey(
                workspace_id=WORKSPACE_ID,
                key_hash=hash_key(API_KEY),
                key_material=API_KEY,
            )
        )
        session.add(
            RetellLLM(
                llm_id=LLM_ID,
                workspace_id=WORKSPACE_ID,
                general_prompt="You are Clara. Caller: {{first_name}}.",
                begin_message="{{bm_greeting}}",
                general_tools=[
                    {
                        "type": "custom",
                        "name": "schedule_callback",
                        "description": "Schedule a callback",
                        "url": "https://consumer.example/functions/v1/schedule-callback",
                        "method": "POST",
                        "parameters": {
                            "type": "object",
                            "properties": {"phone": {"type": "string"}},
                            "required": ["phone"],
                        },
                    }
                ],
            )
        )
        for agent_id, name in ((AGENT_ID, "Sales"), (COMPANION_AGENT_ID, "Companion")):
            session.add(
                Agent(
                    agent_id=agent_id,
                    workspace_id=WORKSPACE_ID,
                    agent_name=name,
                    response_engine={"type": "retell-llm", "llm_id": LLM_ID},
                    voice_id="cartesia-sonic",
                    webhook_url=None,
                )
            )
        session.add(
            PhoneNumber(
                phone_number=FROM_NUMBER,
                workspace_id=WORKSPACE_ID,
                nickname="Telnyx Main",
                inbound_agent_id=COMPANION_AGENT_ID,
                outbound_agent_id=COMPANION_AGENT_ID,
                inbound_webhook_url=None,
            )
        )
        await session.commit()
    yield
    # Drain fire-and-forget webhook/analysis tasks before tearing down, so a
    # leaked task can't run against the next test's freshly reset schema.
    while webhooks.background_tasks:
        await asyncio.gather(*list(webhooks.background_tasks), return_exceptions=True)
    # Batch drainers poll for minutes; cancel instead of draining.
    for task in list(batch_calls._drain_tasks):
        task.cancel()
    if batch_calls._drain_tasks:
        await asyncio.gather(*list(batch_calls._drain_tasks), return_exceptions=True)
    await engine.dispose()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _stub_telephony(monkeypatch):
    """No LiveKit in tests: creating a call succeeds without dialing."""

    async def _noop(call):
        return None

    monkeypatch.setattr("arhiteq_api.services.telephony.start_outbound_call", _noop)
    monkeypatch.setattr("arhiteq_api.services.telephony.dispatch_agent", _noop)


OTHER_API_KEY = "key_other_0123456789abcdef0123456789ab"
OTHER_WORKSPACE_ID = "ws_other00000000000000000"
OTHER_AUTH_HEADERS = {"Authorization": f"Bearer {OTHER_API_KEY}"}


@pytest.fixture
async def other_workspace():
    """A second seeded workspace + API key, for cross-workspace isolation tests."""
    async with db_module.session_factory()() as session:
        session.add(Workspace(id=OTHER_WORKSPACE_ID, name="Other", webhook_url=None))
        session.add(
            ApiKey(
                workspace_id=OTHER_WORKSPACE_ID,
                key_hash=hash_key(OTHER_API_KEY),
                key_material=OTHER_API_KEY,
            )
        )
        await session.commit()
    return OTHER_WORKSPACE_ID
