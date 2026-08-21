"""The metrics views, executed as plain selects.

They install as Postgres views in production, but they are dialect-free
selects, so the suite runs the identical SQL against the SQLite test database
and asserts what the numbers mean.
"""

import pytest

from arhiteq_api.db import session_factory
from arhiteq_api.models import Agent, AgentVersion, Call, Workspace, WorkspaceMember
from arhiteq_api.services.metrics_views import (
    apply_metrics_views,
    tenancy_select,
    workspace_daily_select,
)

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


async def test_workspace_daily_excludes_zero_duration_calls_from_minutes(session):
    """A failed dial consumed no audio, but it is still a call that happened.

    The gap between `calls` and `minutes` is the thing an operator must be
    able to see; dropping the row would hide it.
    """
    session.add(Workspace(id="ws_dur", name="Duration"))
    session.add_all(
        [
            Call(
                call_id="call_connected",
                workspace_id="ws_dur",
                agent_id="ag",
                direction="outbound",
                start_timestamp=_MS_PER_DAY,
                duration_ms=120_000,
            ),
            Call(
                call_id="call_never_answered",
                workspace_id="ws_dur",
                agent_id="ag",
                direction="outbound",
                start_timestamp=_MS_PER_DAY,
                duration_ms=0,
            ),
        ]
    )
    await session.commit()

    rows = (await session.execute(workspace_daily_select())).mappings().all()
    row = next(r for r in rows if r["workspace_id"] == "ws_dur")

    assert row["calls"] == 2
    assert row["connected_calls"] == 1
    assert row["unconnected_calls"] == 1
    assert row["minutes"] == pytest.approx(2.0)


async def test_workspace_daily_buckets_by_utc_day_of_the_call(session):
    """Two calls 25 hours apart are two days, whatever `created_at_ms` says."""
    session.add(Workspace(id="ws_days", name="Days"))
    session.add_all(
        [
            Call(
                call_id="call_day_one",
                workspace_id="ws_days",
                agent_id="ag",
                direction="inbound",
                start_timestamp=10 * _MS_PER_DAY + 1,
                duration_ms=60_000,
            ),
            Call(
                call_id="call_day_two",
                workspace_id="ws_days",
                agent_id="ag",
                direction="inbound",
                start_timestamp=11 * _MS_PER_DAY + 1,
                duration_ms=60_000,
            ),
        ]
    )
    await session.commit()

    rows = [
        r
        for r in (await session.execute(workspace_daily_select())).mappings().all()
        if r["workspace_id"] == "ws_days"
    ]

    assert sorted(r["day_ms"] for r in rows) == [10 * _MS_PER_DAY, 11 * _MS_PER_DAY]


async def test_workspace_daily_counts_turns_only_where_latency_was_recorded(session):
    """Turn data starts partway through history, so coverage is reported too.

    `calls_with_turns` vs `calls` is what lets a panel say "N of M calls"
    instead of implying the platform went quiet before #261.
    """
    session.add(Workspace(id="ws_turns", name="Turns"))
    session.add_all(
        [
            Call(
                call_id="call_with_latency",
                workspace_id="ws_turns",
                agent_id="ag",
                direction="inbound",
                start_timestamp=20 * _MS_PER_DAY,
                duration_ms=60_000,
                latency={"e2e": {"p50": 900.0, "p95": 1200.0, "max": 1500.0, "num": 7}},
            ),
            Call(
                call_id="call_without_latency",
                workspace_id="ws_turns",
                agent_id="ag",
                direction="inbound",
                start_timestamp=20 * _MS_PER_DAY,
                duration_ms=60_000,
                latency=None,
            ),
        ]
    )
    await session.commit()

    rows = (await session.execute(workspace_daily_select())).mappings().all()
    row = next(r for r in rows if r["workspace_id"] == "ws_turns")

    assert row["calls"] == 2
    assert row["calls_with_turns"] == 1
    assert row["llm_turns"] == 7
