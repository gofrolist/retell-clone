"""Grafana's read model: the `metrics` schema.

Views answering how much the platform is used, how hard it is pushed, and what
serving each account costs. They exist as views rather than as panel SQL so
that every panel agrees on what a "connected call" or "a call's model" is, and
so `grafana_ro` can be granted the answers without being granted the tables
underneath them -- `calls.transcript` holds customer conversation content, and
a role with SELECT on `calls` can read all of it.

Everything here is a dialect-free select: the suite executes these against
SQLite and boot compiles them into Postgres views, so the numbers the tests
assert are the numbers the dashboard draws. That rules out LATERAL, DISTINCT
ON, FILTER and generate_series, and it is why buckets are computed with integer
arithmetic on epoch milliseconds instead of date_trunc.

Times are epoch milliseconds, like every other time column in this schema.
Panels convert with to_timestamp(x/1000); a view never does, because
to_timestamp does not exist in SQLite.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import BigInteger, Select, String, cast, func, literal, null, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentVersion, PhoneNumber, Workspace, WorkspaceMember
from .view_ddl import apply_views, render_view_sql

SCHEMA = "metrics"

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000
_MS_PER_MINUTE = 60_000.0


def _bucket(column: Any, size: int) -> Any:
    """Epoch-ms truncated down to a bucket boundary.

    Integer division, which both Postgres and SQLite truncate toward zero.
    Every timestamp here is positive, so truncation is floor. date_trunc would
    be clearer and does not exist in SQLite; float division would introduce a
    rounding question at bucket edges that integers do not have.
    """
    return (column / size) * size


def tenancy_select() -> Select[Any]:
    """One row per workspace, member, agent and phone number.

    `created_at_ms` is NULL where the schema records no creation time, rather
    than a stand-in: `phone_numbers` and `agents` carry only
    `last_modification_timestamp`, and drawing an adoption curve from an edit
    timestamp invents history. An agent is dated from its first version
    instead, which is a real creation time; a phone number has none, so its
    panel is a total rather than a trend.
    """
    agent_created = (
        select(func.min(AgentVersion.created_timestamp))
        .where(AgentVersion.agent_id == Agent.agent_id)
        .correlate(Agent)
        .scalar_subquery()
    )

    workspaces = select(
        literal("workspace").label("entity"),
        Workspace.id.label("workspace_id"),
        Workspace.id.label("entity_id"),
        Workspace.created_at_ms.label("created_at_ms"),
    )
    members = select(
        literal("member"),
        WorkspaceMember.workspace_id,
        # The union's entity_id column is text; this key is an int.
        cast(WorkspaceMember.id, String),
        WorkspaceMember.created_at_ms,
    )
    agents = select(
        literal("agent"),
        Agent.workspace_id,
        Agent.agent_id,
        agent_created,
    )
    phone_numbers = select(
        literal("phone_number"),
        PhoneNumber.workspace_id,
        PhoneNumber.phone_number,
        # An untyped NULL would leave the union column's type ambiguous.
        cast(null(), BigInteger),
    )
    return union_all(workspaces, members, agents, phone_numbers)


VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...] = (("tenancy", tenancy_select),)


async def apply_metrics_views(session: AsyncSession) -> bool:
    """Install every metrics view. Idempotent; a no-op off Postgres."""
    return await apply_views(
        session,
        SCHEMA,
        [
            (f"{SCHEMA}.{name}", render_view_sql(f"{SCHEMA}.{name}", build()))
            for name, build in VIEWS
        ],
    )
