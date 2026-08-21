"""The metrics views, executed as plain selects.

They install as Postgres views in production, but they are dialect-free
selects, so the suite runs the identical SQL against the SQLite test database
and asserts what the numbers mean.
"""

import pytest

from arhiteq_api.db import session_factory
from arhiteq_api.models import Agent, AgentVersion, Workspace, WorkspaceMember
from arhiteq_api.services.metrics_views import apply_metrics_views, tenancy_select

_MS_PER_DAY = 86_400_000


@pytest.fixture
async def session():
    async with session_factory()() as s:
        yield s


async def test_tenancy_reports_one_row_per_entity_with_its_workspace(session):
    session.add(Workspace(id="ws_tenancy", name="Tenancy", created_at_ms=1_000))
    session.add(
        WorkspaceMember(workspace_id="ws_tenancy", email="a@example.com", created_at_ms=2_000)
    )
    await session.commit()

    rows = (await session.execute(tenancy_select())).mappings().all()
    mine = [r for r in rows if r["workspace_id"] == "ws_tenancy"]

    assert {"workspace", "member"} <= {r["entity"] for r in mine}
    workspace_row = next(r for r in mine if r["entity"] == "workspace")
    assert workspace_row["entity_id"] == "ws_tenancy"
    assert workspace_row["created_at_ms"] == 1_000


async def test_tenancy_dates_an_agent_from_its_first_version(session):
    """`agents` has no created_at_ms; version 0's timestamp is the creation."""
    session.add(Workspace(id="ws_agent_age", name="Agent age"))
    session.add(
        Agent(agent_id="ag_dated", workspace_id="ws_agent_age", response_engine={"llm_id": "x"})
    )
    session.add_all(
        [
            AgentVersion(
                agent_id="ag_dated", version=0, workspace_id="ws_agent_age", created_timestamp=500
            ),
            AgentVersion(
                agent_id="ag_dated", version=1, workspace_id="ws_agent_age", created_timestamp=900
            ),
        ]
    )
    await session.commit()

    rows = (await session.execute(tenancy_select())).mappings().all()
    agent_row = next(r for r in rows if r["entity_id"] == "ag_dated")

    assert agent_row["entity"] == "agent"
    assert agent_row["created_at_ms"] == 500


async def test_apply_metrics_views_is_skipped_on_sqlite(session):
    """SQLite has no schemas; the suite must not need a Postgres to run."""
    assert await apply_metrics_views(session) is False
