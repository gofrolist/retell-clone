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

from ..models import (
    Agent,
    AgentVersion,
    Call,
    ConversationFlow,
    CostRate,
    PhoneNumber,
    RetellLLM,
    Workspace,
    WorkspaceMember,
)
from .pricing import cost_columns, price_from_cost, scalar_sources
from .view_ddl import apply_views

SCHEMA = "metrics"

_MS_PER_DAY = 86_400_000
_MS_PER_HOUR = 3_600_000
_MS_PER_MINUTE = 60_000.0

# How far the concurrency views will follow a single call across hour
# boundaries. It bounds the offset list `_hour_offsets` unrolls, so it is a
# cost as well as a limit: every ranged call is matched against this many
# integers. A day is far past any real voice call -- a session that outlives it
# is a stuck worker, and its hours past the 24th go back to being gaps.
_MAX_SPANNED_HOURS = 24


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


def _hour_offsets() -> Any:
    """1..`_MAX_SPANNED_HOURS` as rows, without generate_series.

    A UNION ALL of integer literals, because the boundary events below need a
    small series and generate_series is Postgres-only -- these selects are
    executed against SQLite by the suite.
    """
    return union_all(*[select(literal(n).label("n")) for n in range(1, _MAX_SPANNED_HOURS + 1)])


def _concurrency_events() -> Any:
    """The +1/-1/0 event stream every concurrency view is a running sum over.

    +1 at each start, -1 at each end, and a 0 at every hour boundary a call
    crosses. The zeroes are what make an hour's peak correct rather than merely
    plausible: without them the running total is only ever sampled at events
    that fall *inside* the hour, so concurrency carried in from the previous
    hour is never observed. Three calls running 09:50->10:20 would leave hour 10
    with only the three -1 events -- running values 2, 1, 0 -- and report a peak
    of 2 for an hour whose real peak was 3. An hour a call spans end to end
    would produce no row at all, drawing a gap in the busiest stretch.

    A 0-delta event does not change the running total, so it reads the level
    carried into its hour and nothing else. Ordering by (at_ms, delta) puts it
    after any -1 and before any +1 at the same instant, which is the same
    tie-break convention the rest of this view uses.

    Ties order -1 before +1, so a call ending exactly as the next starts reads
    as one concurrent call rather than two. Calls with no end are excluded
    entirely: their +1 would never be balanced and every later hour would
    inherit the leak.
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
    # An implicit cross join with the offset rows: one boundary event per hour
    # the call is still running at. `< end_timestamp` and not `<=`, because a
    # call that ends exactly on the boundary is not running during the hour
    # that boundary opens.
    offsets = _hour_offsets().subquery("offsets")
    boundary_ms = _bucket(Call.start_timestamp, _MS_PER_HOUR) + offsets.c.n * _MS_PER_HOUR
    boundaries = select(
        Call.workspace_id,
        boundary_ms,
        literal(0),
    ).where(and_(ranged, boundary_ms < Call.end_timestamp))
    return union_all(starts, ends, boundaries).subquery("events")


def _concurrency_hourly(per_workspace: bool) -> Select[Any]:
    """Peak overlapping calls per hour, optionally split by workspace.

    An event stream, not an interval join: a running total over the merged
    stream, and the max of that total per hour. The obvious alternative -- join
    every call to every hour it spans -- answers a different question (calls
    *overlapping* the hour, which 60 back-to-back one-minute calls inflate to
    60) and needs generate_series besides.

    `per_workspace` is not cosmetic. Partitioning the running sum by workspace
    answers "how hard is one account pushing us"; the unpartitioned sum answers
    "how much load is on the platform", and no aggregate over the first gives
    the second -- max() across workspaces returns the largest single-workspace
    peak, and summing them adds peaks that never coincided.

    This is the load answer Prometheus structurally cannot give -- it keeps 15
    days, and the calls table keeps everything.
    """
    events = _concurrency_events()
    running_total = func.sum(events.c.delta).over(
        partition_by=events.c.workspace_id if per_workspace else None,
        order_by=(events.c.at_ms, events.c.delta),
        rows=(None, 0),
    )
    columns = [events.c.at_ms.label("at_ms"), running_total.label("concurrent")]
    if per_workspace:
        columns.insert(0, events.c.workspace_id.label("workspace_id"))
    running = select(*columns).subquery("running")

    hour_ms = _bucket(running.c.at_ms, _MS_PER_HOUR).label("hour_ms")
    peak = func.max(running.c.concurrent).label("peak_concurrent")
    if not per_workspace:
        return select(hour_ms, peak).group_by(hour_ms)
    return select(
        running.c.workspace_id.label("workspace_id"),
        hour_ms,
        peak,
    ).group_by(running.c.workspace_id, hour_ms)


def concurrency_hourly_select() -> Select[Any]:
    """Peak overlapping calls per workspace per hour."""
    return _concurrency_hourly(per_workspace=True)


def concurrency_hourly_total_select() -> Select[Any]:
    """Peak overlapping calls across the whole platform, per hour.

    The capacity-planning number, and the one `concurrency_hourly` cannot be
    aggregated into: five workspaces each peaking at 3 simultaneously put 15
    calls on the platform, while max() over their per-workspace peaks reads 3.
    """
    return _concurrency_hourly(per_workspace=False)


def call_cost_select() -> Select[Any]:
    """One row per call: what it cost us and what it would have earned.

    "Would have earned": nobody is billed, so `implied_price_usd` is this
    call's minutes at the list price the pricing rule computes, not revenue.

    Cost is duration x rate, not metered consumption. `calls.call_cost` exists
    and has never been written, so a call that burned an unusual number of
    tokens costs the same here as a quiet one of equal length. This view is the
    seam where metered actuals replace the estimate later, with no panel
    changing.
    """
    at_ms = func.coalesce(Call.start_timestamp, Call.created_at_ms)

    # Which model ran, most authoritative first. A published version is an
    # immutable snapshot of what actually ran, so it survives the agent being
    # re-pointed at a different model later; the live rows are the fallback for
    # a draft, whose content *is* the live row (docs/AGENT_VERSIONING.md).
    version_llm = (
        select(AgentVersion.llm_snapshot["model"].as_string())
        .where(AgentVersion.agent_id == Call.agent_id, AgentVersion.version == Call.agent_version)
        .correlate(Call)
        .scalar_subquery()
    )
    version_flow = (
        select(AgentVersion.flow_snapshot["model_choice"]["model"].as_string())
        .where(AgentVersion.agent_id == Call.agent_id, AgentVersion.version == Call.agent_version)
        .correlate(Call)
        .scalar_subquery()
    )
    live_llm = (
        select(RetellLLM.model)
        .where(RetellLLM.llm_id == Agent.response_engine["llm_id"].as_string())
        .correlate(Agent)
        .scalar_subquery()
    )
    live_flow = (
        select(ConversationFlow.model_choice["model"].as_string())
        .where(
            ConversationFlow.conversation_flow_id
            == Agent.response_engine["conversation_flow_id"].as_string()
        )
        .correlate(Agent)
        .scalar_subquery()
    )
    # NULL when nothing resolves, which makes the whole cost NULL rather than
    # quietly pricing the call at some default model's rate.
    model_id = func.coalesce(version_llm, version_flow, live_llm, live_flow)

    # Resolve the model and the instant *once*, in their own layer, before any
    # rate lookup references them. Inlining `model_id` directly into
    # scalar_sources puts that four-way coalesce of subqueries inside every one
    # of the ~20 rate lookups the price formula expands to: the compiled view
    # came out at 168KB of SQL, which Postgres would re-parse and re-plan on
    # every panel refresh. Through a subquery it is one column reference.
    resolved = (
        select(
            Call.call_id.label("call_id"),
            Call.workspace_id.label("workspace_id"),
            Call.agent_id.label("agent_id"),
            Call.call_type.label("call_type"),
            Call.direction.label("direction"),
            Call.duration_ms.label("duration_ms"),
            at_ms.label("at_ms"),
            model_id.label("model_id"),
        )
        .select_from(Call)
        .outerjoin(Agent, Agent.agent_id == Call.agent_id)
        .subquery("resolved")
    )

    sources = scalar_sources(resolved.c.model_id, resolved.c.at_ms)
    cost = cost_columns(sources)

    minutes = cast(
        case(
            (resolved.c.duration_ms > 0, resolved.c.duration_ms / _MS_PER_MINUTE),
            else_=0.0,
        ),
        Float,
    )
    # 0.0 for a web call is a real zero, not an unknown: no trunk was used.
    telephony_per_min = case(
        (resolved.c.call_type != "phone_call", literal(0.0)),
        (resolved.c.direction == "inbound", sources.component("telnyx_inbound")),
        (resolved.c.direction == "outbound", sources.component("telnyx_outbound")),
        else_=null(),
    )

    # Second layer, same reason as the first: the price rule references the
    # cost four times over, and the cost is a stack of per-call rate lookups.
    # Computing it here makes it a column the rule can point at.
    costed = select(
        resolved.c.call_id.label("call_id"),
        resolved.c.workspace_id.label("workspace_id"),
        resolved.c.agent_id.label("agent_id"),
        resolved.c.at_ms.label("at_ms"),
        resolved.c.call_type.label("call_type"),
        resolved.c.direction.label("direction"),
        resolved.c.model_id.label("model_id"),
        minutes.label("minutes"),
        cost.cost_per_min_stack.label("cost_per_min"),
        (minutes * telephony_per_min).label("telephony_cost_usd"),
    ).subquery("costed")

    rule = price_from_cost(costed.c.cost_per_min, scalar_sources(costed.c.model_id, costed.c.at_ms))
    # `model_cost` and not `variable_cost`: the trunk is per-minute and varies
    # with the call too, so "variable" would name both this and the total. The
    # split the dashboard actually draws is per-call cost (this + telephony)
    # against allocated fixed infrastructure.
    model_cost = costed.c.minutes * costed.c.cost_per_min
    total_cost = model_cost + costed.c.telephony_cost_usd
    implied_price = costed.c.minutes * rule.price_per_min

    return select(
        costed.c.call_id.label("call_id"),
        costed.c.workspace_id.label("workspace_id"),
        costed.c.agent_id.label("agent_id"),
        _bucket(costed.c.at_ms, _MS_PER_DAY).label("day_ms"),
        costed.c.call_type.label("call_type"),
        costed.c.direction.label("direction"),
        costed.c.model_id.label("model_id"),
        costed.c.minutes.label("minutes"),
        costed.c.cost_per_min.label("cost_per_min"),
        rule.price_per_min.label("price_per_min"),
        rule.rule_source.label("rule_source"),
        model_cost.label("model_cost_usd"),
        costed.c.telephony_cost_usd.label("telephony_cost_usd"),
        total_cost.label("total_cost_usd"),
        implied_price.label("implied_price_usd"),
        (implied_price - total_cost).label("implied_margin_usd"),
    )


def fixed_cost_select() -> Select[Any]:
    """The monthly costs that are not per-call: infrastructure, DID rental.

    Exists so the dashboard can allocate fixed cost without `grafana_ro`
    holding a grant on `cost_rates` -- that table also carries the per-minute
    STT/TTS/telephony rates our margin is computed from, and the pricing domain
    deliberately keeps those unreachable.

    Every effective-dated row is exposed rather than just the current one, and
    the caller picks: a view cannot ask Postgres for "now" without a now()
    expression that SQLite has no answer for, and the panel already knows which
    instant its window ends at.
    """
    return select(
        CostRate.component.label("component"),
        CostRate.unit_price_usd.label("monthly_usd"),
        CostRate.effective_from_ms.label("effective_from_ms"),
    ).where(CostRate.unit == "per_month")


VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...] = (
    ("tenancy", tenancy_select),
    ("workspace_daily", workspace_daily_select),
    ("call_cost", call_cost_select),
    ("fixed_cost", fixed_cost_select),
    ("concurrency_hourly", concurrency_hourly_select),
    ("concurrency_hourly_total", concurrency_hourly_total_select),
)


async def apply_metrics_views(session: AsyncSession) -> bool:
    """Install every metrics view. Idempotent; a no-op off Postgres."""
    return await apply_views(
        session, SCHEMA, [(f"{SCHEMA}.{name}", build()) for name, build in VIEWS]
    )
