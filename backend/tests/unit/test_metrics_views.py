"""The metrics views, executed as plain selects.

They install as Postgres views in production, but they are dialect-free
selects, so the suite runs the identical SQL against the SQLite test database
and asserts what the numbers mean.
"""

import pytest

from arhiteq_api.db import session_factory
from arhiteq_api.models import Agent, AgentVersion, Call, RetellLLM, Workspace, WorkspaceMember
from arhiteq_api.services.metrics_views import (
    apply_metrics_views,
    call_cost_select,
    concurrency_hourly_select,
    fixed_cost_select,
    tenancy_select,
    workspace_daily_select,
)
from arhiteq_api.services.pricing_seed import seed_pricing_defaults

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


async def test_concurrency_counts_an_overlapping_pair_as_two(session):
    session.add(Workspace(id="ws_overlap", name="Overlap"))
    base = 30 * _MS_PER_DAY
    session.add_all(
        [
            Call(
                call_id="call_overlap_a",
                workspace_id="ws_overlap",
                agent_id="ag",
                direction="inbound",
                start_timestamp=base,
                end_timestamp=base + 60_000,
                duration_ms=60_000,
            ),
            Call(
                call_id="call_overlap_b",
                workspace_id="ws_overlap",
                agent_id="ag",
                direction="inbound",
                start_timestamp=base + 30_000,
                end_timestamp=base + 90_000,
                duration_ms=60_000,
            ),
        ]
    )
    await session.commit()

    rows = (await session.execute(concurrency_hourly_select())).mappings().all()
    peak = max(r["peak_concurrent"] for r in rows if r["workspace_id"] == "ws_overlap")

    assert peak == 2


async def test_concurrency_counts_an_adjacent_pair_as_one(session):
    """One call ending exactly as the next starts is one call at a time.

    This is what decides the tie-break: at a shared instant the -1 must be
    applied before the +1, or a busy sequential queue reads as double the
    concurrency it actually had.
    """
    session.add(Workspace(id="ws_adjacent", name="Adjacent"))
    base = 31 * _MS_PER_DAY
    session.add_all(
        [
            Call(
                call_id="call_adjacent_a",
                workspace_id="ws_adjacent",
                agent_id="ag",
                direction="inbound",
                start_timestamp=base,
                end_timestamp=base + 60_000,
                duration_ms=60_000,
            ),
            Call(
                call_id="call_adjacent_b",
                workspace_id="ws_adjacent",
                agent_id="ag",
                direction="inbound",
                start_timestamp=base + 60_000,
                end_timestamp=base + 120_000,
                duration_ms=60_000,
            ),
        ]
    )
    await session.commit()

    rows = (await session.execute(concurrency_hourly_select())).mappings().all()
    peak = max(r["peak_concurrent"] for r in rows if r["workspace_id"] == "ws_adjacent")

    assert peak == 1


async def test_concurrency_ignores_a_call_that_never_ended(session):
    """An in-flight call has no end event, so it would never be decremented."""
    session.add(Workspace(id="ws_inflight", name="In flight"))
    session.add(
        Call(
            call_id="call_in_flight",
            workspace_id="ws_inflight",
            agent_id="ag",
            direction="inbound",
            start_timestamp=32 * _MS_PER_DAY,
            end_timestamp=None,
            duration_ms=None,
        )
    )
    await session.commit()

    rows = (await session.execute(concurrency_hourly_select())).mappings().all()

    assert [r for r in rows if r["workspace_id"] == "ws_inflight"] == []


async def test_call_cost_prices_a_call_from_the_version_that_ran(session):
    """An agent re-pointed at a new model must not restate old calls."""
    session.add(Workspace(id="ws_cost", name="Cost"))
    session.add(
        Agent(agent_id="ag_cost", workspace_id="ws_cost", response_engine={"llm_id": "llm_now"})
    )
    session.add(RetellLLM(llm_id="llm_now", workspace_id="ws_cost", model="gemini-2.5-flash"))
    session.add(
        AgentVersion(
            agent_id="ag_cost",
            version=3,
            workspace_id="ws_cost",
            is_published=True,
            llm_snapshot={"model": "gemini-live-2.5-flash-native-audio"},
        )
    )
    session.add(
        Call(
            call_id="call_versioned",
            workspace_id="ws_cost",
            agent_id="ag_cost",
            agent_version=3,
            call_type="phone_call",
            direction="outbound",
            start_timestamp=40 * _MS_PER_DAY,
            end_timestamp=40 * _MS_PER_DAY + 60_000,
            duration_ms=60_000,
        )
    )
    await session.commit()

    rows = (await session.execute(call_cost_select())).mappings().all()
    row = next(r for r in rows if r["call_id"] == "call_versioned")

    assert row["model_id"] == "gemini-live-2.5-flash-native-audio"
    assert row["minutes"] == pytest.approx(1.0)


async def test_call_cost_charges_the_trunk_only_for_phone_calls(session):
    """A web call never touches the trunk; charging it overstates the cost of
    the calls that are cheapest to serve."""
    await seed_pricing_defaults(session)
    session.add(Workspace(id="ws_trunk", name="Trunk"))
    session.add(
        Agent(agent_id="ag_trunk", workspace_id="ws_trunk", response_engine={"llm_id": "llm_trunk"})
    )
    session.add(RetellLLM(llm_id="llm_trunk", workspace_id="ws_trunk", model="gemini-2.5-flash"))
    session.add_all(
        [
            Call(
                call_id="call_web",
                workspace_id="ws_trunk",
                agent_id="ag_trunk",
                call_type="web_call",
                direction="inbound",
                start_timestamp=41 * _MS_PER_DAY,
                end_timestamp=41 * _MS_PER_DAY + 60_000,
                duration_ms=60_000,
            ),
            Call(
                call_id="call_phone",
                workspace_id="ws_trunk",
                agent_id="ag_trunk",
                call_type="phone_call",
                direction="inbound",
                start_timestamp=41 * _MS_PER_DAY,
                end_timestamp=41 * _MS_PER_DAY + 60_000,
                duration_ms=60_000,
            ),
        ]
    )
    await session.commit()

    rows = {
        r["call_id"]: r
        for r in (await session.execute(call_cost_select())).mappings().all()
        if r["workspace_id"] == "ws_trunk"
    }

    assert rows["call_web"]["telephony_cost_usd"] == pytest.approx(0.0)
    assert rows["call_phone"]["telephony_cost_usd"] > 0
    # The trunk is the only difference between them.
    assert rows["call_phone"]["total_cost_usd"] > rows["call_web"]["total_cost_usd"]
    # The price rule reaches the outer layer: the seed carries a global markup,
    # so a priced call must show a rule and a margin above its cost.
    assert rows["call_phone"]["rule_source"] == "global"
    assert rows["call_phone"]["implied_margin_usd"] > 0


async def test_call_cost_is_null_for_a_call_whose_model_has_no_rate(session):
    """Unknown stays unknown: a 0 would report an unpriced model as free."""
    session.add(Workspace(id="ws_unpriced", name="Unpriced"))
    session.add(
        Agent(
            agent_id="ag_unpriced",
            workspace_id="ws_unpriced",
            response_engine={"llm_id": "llm_unpriced"},
        )
    )
    session.add(
        RetellLLM(llm_id="llm_unpriced", workspace_id="ws_unpriced", model="model-with-no-rate")
    )
    session.add(
        Call(
            call_id="call_unpriced",
            workspace_id="ws_unpriced",
            agent_id="ag_unpriced",
            call_type="phone_call",
            direction="inbound",
            start_timestamp=42 * _MS_PER_DAY,
            end_timestamp=42 * _MS_PER_DAY + 60_000,
            duration_ms=60_000,
        )
    )
    await session.commit()

    rows = (await session.execute(call_cost_select())).mappings().all()
    row = next(r for r in rows if r["call_id"] == "call_unpriced")

    assert row["total_cost_usd"] is None
    assert row["implied_margin_usd"] is None


async def test_call_cost_gives_an_unconnected_call_no_minutes(session):
    await seed_pricing_defaults(session)
    session.add(Workspace(id="ws_nodial", name="No dial"))
    session.add(
        Agent(agent_id="ag_nodial", workspace_id="ws_nodial", response_engine={"llm_id": "llm_nd"})
    )
    session.add(RetellLLM(llm_id="llm_nd", workspace_id="ws_nodial", model="gemini-2.5-flash"))
    session.add(
        Call(
            call_id="call_nodial",
            workspace_id="ws_nodial",
            agent_id="ag_nodial",
            call_type="phone_call",
            direction="outbound",
            start_timestamp=43 * _MS_PER_DAY,
            duration_ms=0,
        )
    )
    await session.commit()

    rows = (await session.execute(call_cost_select())).mappings().all()
    row = next(r for r in rows if r["call_id"] == "call_nodial")

    assert row["minutes"] == pytest.approx(0.0)
    assert row["telephony_cost_usd"] == pytest.approx(0.0)


async def test_fixed_cost_exposes_monthly_rates_without_the_per_minute_ones(session):
    """The dashboard allocates fixed cost; it must not need `cost_rates`.

    A grant on that table would also hand over the per-minute STT, TTS and
    telephony rates our margin is derived from, which the pricing domain keeps
    unreachable on purpose.
    """
    await seed_pricing_defaults(session)

    rows = (await session.execute(fixed_cost_select())).mappings().all()
    components = {r["component"] for r in rows}

    assert "infra_fixed_monthly" in components
    assert components.isdisjoint({"cartesia_stt", "cartesia_tts", "telnyx_inbound"})
    infra = next(r for r in rows if r["component"] == "infra_fixed_monthly")
    assert infra["monthly_usd"] > 0
