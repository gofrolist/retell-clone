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

from sqlalchemy import (
    BigInteger,
    Float,
    Select,
    String,
    and_,
    case,
    cast,
    func,
    literal,
    null,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Agent, AgentVersion, Call, PhoneNumber, Workspace, WorkspaceMember
from .view_ddl import apply_views

SCHEMA = "metrics"

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000
_MS_PER_MINUTE = 60_000.0


def _bucket(column: Any, size: int) -> Any:
    """Epoch-ms truncated down to a bucket boundary.

    Floor division (`//`), not `/`: SQLAlchemy 2.0 renders `/` as *true*
    division, so `ts / 86400000 * 86400000` comes back as a float that is a
    hair under the boundary it is supposed to land on -- day 10 renders as
    863999999.9999999, and every bucket edge becomes its own group. `//`
    renders the dialect's floor division instead and lands exactly.

    date_trunc would be clearer and does not exist in SQLite, which is what
    lets these selects be tested at all.
    """
    return (column // size) * size


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


def workspace_daily_select() -> Select[Any]:
    """One row per workspace per UTC day.

    Bucketed on when the call happened, not when its row was written:
    `created_at_ms` is set at registration, which for an outbound batch can be
    a different day than the dial.
    """
    at_ms = func.coalesce(Call.start_timestamp, Call.created_at_ms)
    day_ms = _bucket(at_ms, _MS_PER_DAY).label("day_ms")
    # CONTRACT (worker/state.py): duration_ms is answer->hangup talk time and
    # is 0 for a call that never connected, never NULL.
    connected = Call.duration_ms > 0
    # latency.e2e.num is the LLM turn count. NULL on every call before #261,
    # which is why the coverage counter next to it exists.
    turns = Call.latency["e2e"]["num"].as_integer()
    return select(
        Call.workspace_id.label("workspace_id"),
        day_ms,
        func.count().label("calls"),
        func.sum(case((connected, 1), else_=0)).label("connected_calls"),
        func.sum(case((connected, 0), else_=1)).label("unconnected_calls"),
        func.sum(case((Call.direction == "inbound", 1), else_=0)).label("inbound_calls"),
        func.sum(case((Call.direction == "outbound", 1), else_=0)).label("outbound_calls"),
        func.sum(case((Call.call_status == "error", 1), else_=0)).label("error_calls"),
        cast(func.sum(case((connected, Call.duration_ms), else_=0)) / _MS_PER_MINUTE, Float).label(
            "minutes"
        ),
        func.sum(turns).label("llm_turns"),
        func.sum(case((turns.isnot(None), 1), else_=0)).label("calls_with_turns"),
    ).group_by(Call.workspace_id, day_ms)


def concurrency_hourly_select() -> Select[Any]:
    """Peak overlapping calls per workspace per hour.

    An event stream, not an interval join: +1 at each start, -1 at each end, a
    running total over the merged stream, and the max of that total per hour.
    The obvious alternative -- join every call to every hour it spans -- needs
    generate_series, which SQLite does not have.

    Ties order -1 before +1, so a call ending exactly as the next starts reads
    as one concurrent call rather than two. Calls with no end are excluded
    entirely: their +1 would never be balanced and every later hour would
    inherit the leak.

    This is the load answer Prometheus structurally cannot give -- it keeps 15
    days, and the calls table keeps everything.
    """
    ranged = and_(
        Call.start_timestamp.isnot(None),
        Call.end_timestamp.isnot(None),
        Call.end_timestamp > Call.start_timestamp,
    )
    starts = select(
        Call.workspace_id.label("workspace_id"),
        Call.start_timestamp.label("at_ms"),
        literal(1).label("delta"),
    ).where(ranged)
    ends = select(
        Call.workspace_id,
        Call.end_timestamp,
        literal(-1),
    ).where(ranged)
    events = union_all(starts, ends).subquery("events")

    running = select(
        events.c.workspace_id.label("workspace_id"),
        events.c.at_ms.label("at_ms"),
        func.sum(events.c.delta)
        .over(
            partition_by=events.c.workspace_id,
            order_by=(events.c.at_ms, events.c.delta),
            rows=(None, 0),
        )
        .label("concurrent"),
    ).subquery("running")

    hour_ms = _bucket(running.c.at_ms, _MS_PER_HOUR).label("hour_ms")
    return select(
        running.c.workspace_id.label("workspace_id"),
        hour_ms,
        func.max(running.c.concurrent).label("peak_concurrent"),
    ).group_by(running.c.workspace_id, hour_ms)


VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...] = (
    ("tenancy", tenancy_select),
    ("workspace_daily", workspace_daily_select),
    ("concurrency_hourly", concurrency_hourly_select),
)


async def apply_metrics_views(session: AsyncSession) -> bool:
    """Install every metrics view. Idempotent; a no-op off Postgres."""
    return await apply_views(
        session, SCHEMA, [(f"{SCHEMA}.{name}", build()) for name, build in VIEWS]
    )
