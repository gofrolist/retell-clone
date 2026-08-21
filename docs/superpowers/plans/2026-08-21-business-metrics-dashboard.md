# Business Metrics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An operator Grafana dashboard answering how much the platform is used, how hard it is pushed, and what serving each account costs — built on a `metrics` Postgres schema that a read-only Grafana role can reach without ever touching a base table.

**Architecture:** Four views in a new `metrics` schema, defined as dialect-free SQLAlchemy `Select`s so the same objects execute on SQLite in tests and compile to Postgres views at API boot — the pattern `services/pricing_view.py` already established. Grafana gains a second datasource pointing at Cloud SQL and reads those views as SQL panels. The cost/price arithmetic is **not** rewritten: `services/pricing.py` is refactored so its one implementation can be evaluated either against the current rate card (the existing `pricing.model_price` view) or against the rates in force at a given call's start.

**Tech Stack:** Python 3.14 · SQLAlchemy 2 (async, asyncpg on Postgres, aiosqlite in tests) · FastAPI lifespan DDL · Postgres 16 (Cloud SQL `arhiteq-pg`, private IP `10.145.0.2`) · kube-prometheus-stack 87.16.1 Grafana · Helm

## Global Constraints

- **This is the executable form of `docs/superpowers/specs/2026-08-20-business-metrics-dashboard-design.md`.** Read it first. Where this plan deviates, the deviation is marked **DEVIATION** and says why.
- **Operator-only.** Nothing lands in the Next.js dashboard. No endpoint is added. No Retell wire contract is touched.
- **A missing rate yields NULL, never 0.** An unpriced component reads as *unknown*. Never `coalesce(..., 0)` a cost, price or rate.
- **Dialect-free SQL.** Every view is a SQLAlchemy `Select` that renders on both SQLite and Postgres. No `LATERAL`, no `DISTINCT ON`, no `FILTER`, no `generate_series`, no `to_timestamp` inside a view. Postgres-only expressions are allowed **only** in dashboard panel SQL, never in a view.
- **Views emit epoch milliseconds, never timestamps.** Column names end in `_ms`, matching every other time column in the schema. Panels convert with `to_timestamp(x/1000)`.
- **No new deploy mechanism.** Views install from the FastAPI lifespan, idempotently, like `_apply_column_backfills` and `apply_pricing_view`. This repo has no Alembic.
- **Dashboard JSON carries no `__inputs` block and pins the datasource uid** (`infra/README.md` records why: file provisioning never resolves `${DS_*}` and every panel silently reads "Datasource not found").
- **Secrets never enter a values file.** Credentials go into a k8s Secret applied by `gen-values.sh` and reach Grafana as env vars, so `helm get values` cannot print them.
- Python: 3.14, `uv`. Backend tests: `cd backend && uv run pytest`. Lint/format: `pre-commit run --files <changed>`.
- `main` is protected. Work on a branch; land by squash-merged PR whose title is a conventional commit.

---

## File Structure

**Created:**
- `backend/src/arhiteq_api/services/view_ddl.py` — the one idempotent "install these views" routine (schema creation, lock timeout, concurrent-boot race handling). Extracted from `pricing_view.py` so `metrics` and `pricing` cannot drift in how they handle a losing replica.
- `backend/src/arhiteq_api/services/metrics_views.py` — the four `metrics` selects and their installer.
- `backend/tests/unit/test_metrics_views.py` — semantic tests, executed as plain queries against the SQLite test DB.
- `backend/tests/unit/test_view_ddl.py` — rendering and non-Postgres-skip tests.
- `infra/sql/grafana_ro.sql` — the operator's role + grant script.
- `infra/helm/monitoring/dashboards/arhiteq-business.json` — the dashboard.

**Modified:**
- `backend/src/arhiteq_api/services/pricing.py` — arithmetic extracted into `price_columns(sources)`; two source builders added. No behaviour change.
- `backend/src/arhiteq_api/services/pricing_view.py` — delegates its DDL to `view_ddl.py`.
- `backend/src/arhiteq_api/main.py` — lifespan calls `apply_metrics_views`.
- `infra/helm/monitoring/values.yaml` — Postgres datasource, DB password env var, checksum annotation.
- `infra/helm/monitoring/gen-values.sh` — applies the DB-credential Secret, renders the new placeholders.
- `infra/README.md` — `grafana_ro` runbook under § Grafana access.

---

## Task 1: One idempotent view installer

Both schemas install views at boot with the same subtle race handling. Today that logic exists once, inside `pricing_view.py`, entangled with the pricing select. Extract it before adding a second caller — a copy would rot the first time one side learned about a new SQLSTATE.

**Files:**
- Create: `backend/src/arhiteq_api/services/view_ddl.py`
- Create: `backend/tests/unit/test_view_ddl.py`
- Modify: `backend/src/arhiteq_api/services/pricing_view.py`

**Interfaces:**
- Produces:
  - `render_view_sql(qualified_name: str, stmt: Select[Any]) -> str` — `"CREATE VIEW <name> AS <compiled>"`, Postgres dialect, `literal_binds=True`.
  - `async apply_views(session: AsyncSession, schema: str, views: Sequence[tuple[str, str]]) -> bool` — creates `schema` and installs each `(qualified_name, create_sql)` by DROP-then-CREATE in one transaction. Returns `False` untouched on non-Postgres, `True` otherwise (including the swallowed race and lock-timeout paths).
- Consumes: nothing.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_view_ddl.py`:

```python
import pytest
from sqlalchemy import literal, select

from arhiteq_api.db import session_factory
from arhiteq_api.services.view_ddl import apply_views, render_view_sql


def test_render_view_sql_is_a_plain_create_view_with_no_bind_params():
    """A view cannot carry parameters, so nothing may render as a placeholder."""
    sql = render_view_sql("metrics.example", select(literal(1).label("one")))

    assert sql.startswith("CREATE VIEW metrics.example AS")
    assert "CREATE OR REPLACE" not in sql
    assert "%(" not in sql and "?" not in sql


async def test_apply_views_is_skipped_on_sqlite():
    """SQLite has no schemas; the suite must not need a Postgres to run."""
    async with session_factory()() as session:
        assert await apply_views(session, "metrics", [("metrics.example", "CREATE VIEW ...")]) is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_view_ddl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.view_ddl'`

- [ ] **Step 3: Create the module**

Create `backend/src/arhiteq_api/services/view_ddl.py`. The comments explain decisions that are not visible from the code; keep them.

```python
"""Install compiled SQLAlchemy selects as Postgres views, idempotently, at boot.

Two schemas do this — `pricing` (the cost -> price rule) and `metrics` (the
Grafana read model) — and both run on every API replica at once. The failure
modes are identical and non-obvious, so they are handled here once rather than
copied: a losing replica in a concurrent-DDL race must treat the winner's
identical view as success, and a Grafana query holding a read lock must not be
able to hang a pod's startup.
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# A few seconds, not Postgres's default (unlimited): long enough to not fire on
# ordinary contention, short enough that a stuck Grafana query cannot hang the
# FastAPI lifespan past a health-check grace period. SET LOCAL, so it never
# leaks onto a pooled connection's later, unrelated queries.
_LOCK_TIMEOUT_MS = 5_000
_SET_LOCK_TIMEOUT_SQL = f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT_MS}ms'"

# SQLSTATEs a losing replica can surface when this boot DDL races another
# replica's identical DDL. Which one a given loser hits depends on timing and
# Postgres version, so all are treated as success:
#   23505 unique_violation  -- duplicate key on a system-catalog index
#   42P06 duplicate_schema
#   42P07 duplicate_table   -- CREATE VIEW has no IF NOT EXISTS
#   42710 duplicate_object
#   40001 serialization failure ("tuple concurrently updated")
# Everything else still fails loudly: a syntax error in generated SQL (42601)
# is a real bug, and a boot that quietly leaves no view means Grafana reads
# stale or absent data with no signal.
_RACE_SQLSTATES = frozenset({"23505", "42P06", "42P07", "42710", "40001"})

# What `_SET_LOCK_TIMEOUT_SQL` turns a blocked wait into. Unlike the races
# above this is not "another replica already did the job" -- it is "a reader is
# holding the lock this DDL needs" -- but the outcome for boot is the same: the
# view from a previous boot is already there and correct.
_LOCK_TIMEOUT_SQLSTATE = "55P03"


def _sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


def render_view_sql(qualified_name: str, stmt: Select[Any]) -> str:
    """Compile `stmt` to a standalone CREATE VIEW for Postgres.

    `literal_binds` because a view cannot carry parameters: anything left as a
    placeholder would be a syntax error at CREATE time.
    """
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return f"CREATE VIEW {qualified_name} AS {compiled}"


async def apply_views(
    session: AsyncSession, schema: str, views: Sequence[tuple[str, str]]
) -> bool:
    """Install `views` (qualified_name, create_sql) into `schema`. Idempotent.

    DROP-then-CREATE, not CREATE OR REPLACE: Postgres refuses to replace a view
    whose column names, types or order changed (42P16), so with OR REPLACE any
    future edit to a select's shape would crash every API replica at boot.
    Dropping first cannot hit that error at all. Both statements run in one
    transaction and Postgres holds the ACCESS EXCLUSIVE lock until commit, so a
    concurrent reader waits rather than seeing the view momentarily absent.

    Grants survive this only because the operator sets ALTER DEFAULT PRIVILEGES
    (see infra/sql/grafana_ro.sql); a plain GRANT would be dropped along with
    the view on the next boot.
    """
    if session.get_bind().dialect.name != "postgresql":
        return False
    try:
        await session.execute(text(_SET_LOCK_TIMEOUT_SQL))
        await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        for qualified_name, create_sql in views:
            await session.execute(text(f"DROP VIEW IF EXISTS {qualified_name}"))
            await session.execute(text(create_sql))
        await session.commit()
    except DBAPIError as error:
        state = _sqlstate(error)
        if state == _LOCK_TIMEOUT_SQLSTATE:
            # Non-fatal by design, but logged unlike the races below: it is a
            # real signal an operator may want to act on (a long-running query
            # holding a view's lock) rather than routine boot noise.
            logger.warning(
                "%s view refresh timed out waiting for a lock after %dms; "
                "leaving the existing views from a previous boot in place",
                schema,
                _LOCK_TIMEOUT_MS,
            )
            await session.rollback()
            return True
        if state not in _RACE_SQLSTATES:
            raise
        # A Postgres error poisons the transaction until rolled back, so this
        # must happen before returning -- otherwise the lifespan's next startup
        # step fails with "current transaction is aborted" even though the
        # winning replica created exactly what we wanted.
        await session.rollback()
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_view_ddl.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Point `pricing_view.py` at the shared installer**

In `backend/src/arhiteq_api/services/pricing_view.py`, delete `_LOCK_TIMEOUT_MS`, `_SET_LOCK_TIMEOUT_SQL`, `_DROP_SQL`, `_RACE_SQLSTATES`, `_LOCK_TIMEOUT_SQLSTATE`, `_is_concurrent_ddl_race`, `_is_lock_timeout`, and the body of `apply_pricing_view`. Keep `VIEW_NAME`, `_NOW_MS`, and the module docstring. The file becomes:

```python
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from .pricing import model_price_select
from .view_ddl import apply_views, render_view_sql

VIEW_NAME = "pricing.model_price"

# A SQL expression, not a Python int: passing now_ms() would inline a
# millisecond literal at CREATE VIEW time and freeze the view's idea of "now"
# until someone redeploys, so a provider reprice would never reach the
# dashboard on its own. func.now() is evaluated by Postgres on every query
# instead, so the view always shows the rate in force right now.
_NOW_MS = func.floor(func.extract("epoch", func.now()) * 1000)


def render_pricing_view_sql() -> str:
    return render_view_sql(VIEW_NAME, model_price_select(at_ms=_NOW_MS))


async def apply_pricing_view(session: AsyncSession) -> bool:
    return await apply_views(session, "pricing", [(VIEW_NAME, render_pricing_view_sql())])
```

- [ ] **Step 6: Run the pricing tests to prove nothing moved**

Run: `cd backend && uv run pytest tests/unit/test_pricing_view.py tests/unit/test_pricing.py -v`
Expected: PASS — the existing assertions (plain `CREATE VIEW`, no bind params, assumptions read per query, `False` on SQLite) all still hold.

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no new failures.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/services/view_ddl.py \
        backend/src/arhiteq_api/services/pricing_view.py \
        backend/tests/unit/test_view_ddl.py
git commit -m "refactor(api): extract the boot view installer both schemas need"
```

---

## Task 2: `metrics.tenancy` — adoption

One row per entity instance, carrying its creation time where the schema records one. **DEVIATION from the spec:** the spec says "creation timestamps for workspaces, members, agents and phone numbers". Only `workspaces` and `workspace_members` have a `created_at_ms`. `agents` and `phone_numbers` carry only `last_modification_timestamp`, which is not a creation time and would draw a false trend. An agent's creation is recovered from `min(agent_versions.created_timestamp)`; a phone number has no recorded creation time at all and gets NULL, per the plan-wide NULL-not-zero rule. Panels count rows for totals and filter `created_at_ms IS NOT NULL` for trends.

**Files:**
- Create: `backend/src/arhiteq_api/services/metrics_views.py`
- Create: `backend/tests/unit/test_metrics_views.py`
- Modify: `backend/src/arhiteq_api/main.py`

**Interfaces:**
- Consumes: `view_ddl.apply_views`, `view_ddl.render_view_sql` (Task 1).
- Produces:
  - `SCHEMA: str = "metrics"`
  - `tenancy_select() -> Select[Any]` — columns `entity, workspace_id, entity_id, created_at_ms`.
  - `VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...]` — the registry later tasks append to.
  - `async apply_metrics_views(session: AsyncSession) -> bool`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_metrics_views.py`:

```python
"""The metrics views, executed as plain selects.

They install as Postgres views in production, but they are dialect-free
selects, so the suite runs the identical SQL against the SQLite test database
and asserts what the numbers mean.
"""

import pytest
from sqlalchemy import select

from arhiteq_api.db import session_factory
from arhiteq_api.models import Agent, AgentVersion, Call, Workspace, WorkspaceMember
from arhiteq_api.services.metrics_views import apply_metrics_views, tenancy_select


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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.metrics_views'`

- [ ] **Step 3: Create the module with the tenancy select**

Create `backend/src/arhiteq_api/services/metrics_views.py`:

```python
"""Grafana's read model: the `metrics` schema.

Four views answering how much the platform is used, how hard it is pushed, and
what serving each account costs. They exist as views rather than as panel SQL
so that every panel agrees on what a "connected call" or "a call's model" is,
and so `grafana_ro` can be granted the answers without being granted the
tables underneath them -- `calls.transcript` holds customer conversation
content, and a role with SELECT on `calls` can read all of it.

Everything here is a dialect-free select: the suite executes these against
SQLite and boot compiles them into Postgres views, so the numbers the tests
assert are the numbers the dashboard draws. That rules out LATERAL, DISTINCT
ON, FILTER and generate_series, and it is why buckets are computed with
integer arithmetic on epoch milliseconds instead of date_trunc.

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
    instead, which is a real creation time; a phone number has none.
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
        # Untyped NULL would make the union column's type ambiguous.
        cast(null(), BigInteger),
    )
    return union_all(workspaces, members, agents, phone_numbers)


VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...] = (("tenancy", tenancy_select),)


async def apply_metrics_views(session: AsyncSession) -> bool:
    """Install every metrics view. Idempotent; a no-op off Postgres."""
    return await apply_views(
        session,
        SCHEMA,
        [(f"{SCHEMA}.{name}", render_view_sql(f"{SCHEMA}.{name}", build())) for name, build in VIEWS],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire it into boot**

In `backend/src/arhiteq_api/main.py`, add the import next to the pricing one (line ~40):

```python
from .services.metrics_views import apply_metrics_views
```

and call it in `lifespan`, immediately after `apply_pricing_view` — the order matters, because `metrics.call_cost` (Task 6) reads the pricing view:

```python
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await apply_pricing_view(session)
        await apply_metrics_views(session)
```

- [ ] **Step 6: Run the suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS, no new failures.

- [ ] **Step 7: Commit**

```bash
git add backend/src/arhiteq_api/services/metrics_views.py \
        backend/src/arhiteq_api/main.py \
        backend/tests/unit/test_metrics_views.py
git commit -m "feat(api): add the metrics schema and a tenancy view"
```

---

## Task 3: `metrics.workspace_daily` — load and usage

One row per workspace per day: volume, connectedness, minutes, direction split, errors, and LLM turns.

**Files:**
- Modify: `backend/src/arhiteq_api/services/metrics_views.py`
- Modify: `backend/tests/unit/test_metrics_views.py`

**Interfaces:**
- Produces: `workspace_daily_select() -> Select[Any]` — columns `workspace_id, day_ms, calls, connected_calls, unconnected_calls, inbound_calls, outbound_calls, error_calls, minutes, llm_turns, calls_with_turns`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_metrics_views.py` (and add `workspace_daily_select` to the import from `arhiteq_api.services.metrics_views`):

```python
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
```

Add `_MS_PER_DAY = 86_400_000` at the top of the test module, and `Call` to the model imports.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: FAIL — `ImportError: cannot import name 'workspace_daily_select'`

- [ ] **Step 3: Implement the select**

In `backend/src/arhiteq_api/services/metrics_views.py`, add `Call` to the model imports, add `Float` and `case` to the SQLAlchemy imports, and add:

```python
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
        cast(
            func.sum(case((connected, Call.duration_ms), else_=0)) / _MS_PER_MINUTE, Float
        ).label("minutes"),
        func.sum(turns).label("llm_turns"),
        func.sum(case((turns.isnot(None), 1), else_=0)).label("calls_with_turns"),
    ).group_by(Call.workspace_id, day_ms)
```

Register it in `VIEWS`:

```python
VIEWS: tuple[tuple[str, Callable[[], Select[Any]]], ...] = (
    ("tenancy", tenancy_select),
    ("workspace_daily", workspace_daily_select),
)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/arhiteq_api/services/metrics_views.py backend/tests/unit/test_metrics_views.py
git commit -m "feat(api): add the workspace_daily metrics view"
```

---

## Task 4: `metrics.concurrency_hourly` — peak load

Peak overlapping calls per hour. This is the load answer Prometheus structurally cannot give: it keeps 15 days, and the calls table keeps everything.

**Files:**
- Modify: `backend/src/arhiteq_api/services/metrics_views.py`
- Modify: `backend/tests/unit/test_metrics_views.py`

**Interfaces:**
- Produces: `concurrency_hourly_select() -> Select[Any]` — columns `workspace_id, hour_ms, peak_concurrent`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_metrics_views.py` (add `concurrency_hourly_select` to the imports):

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: FAIL — `ImportError: cannot import name 'concurrency_hourly_select'`

- [ ] **Step 3: Implement the select**

Add `and_` and `union_all` to the SQLAlchemy imports if not already present, then add:

```python
def concurrency_hourly_select() -> Select[Any]:
    """Peak overlapping calls per workspace per hour.

    An event stream, not an interval join: +1 at each start, -1 at each end, a
    running total over the merged stream, and the max of that total per hour.
    The alternative (join every call to every hour it spans) needs
    generate_series, which SQLite does not have.

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
```

Register it in `VIEWS` after `workspace_daily`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/arhiteq_api/services/metrics_views.py backend/tests/unit/test_metrics_views.py
git commit -m "feat(api): add the concurrency_hourly metrics view"
```

---

## Task 5: One price arithmetic, two ways to look up its inputs

`metrics.call_cost` must price each call with the rates in force **at that call's start**, while `pricing.model_price` prices every model at **now**. Same arithmetic, different lookups.

**DEVIATION from the spec:** the spec says `call_cost` joins price "from `pricing.model_price` at `start_timestamp`". That view has no time dimension — it is a snapshot of the current rate card — so the join it describes cannot be written. Rather than duplicate the formula (which `pricing.py`'s docstring forbids, and rightly: the day the two disagree, the price list and the margin report describe the same call differently), the arithmetic is extracted into one function over an abstract set of lookups, and two lookup strategies are supplied.

Note the limit this does not remove: `pricing_assumptions` has no effective dating at all, so an assumption edit still retroactively changes every historical price. Effective-dated assumptions are their own change; this task makes the rate and rule tables historically correct, which is the part the schema supports.

**Files:**
- Modify: `backend/src/arhiteq_api/services/pricing.py`
- Modify: `backend/tests/unit/test_pricing.py`

**Interfaces:**
- Produces:
  - `class PriceSources(NamedTuple)` — `is_audio`, `input_per_1m`, `output_per_1m` (`ColumnElement`s) and `component(name: str)`, `model_rule(column)`, `global_rule(column)` (callables returning `ColumnElement`).
  - `class PriceColumns(NamedTuple)` — `cost_per_min_model`, `cost_per_min_stack`, `price_per_min`, `rule_source` (`ColumnElement`s, unlabeled).
  - `price_columns(sources: PriceSources) -> PriceColumns`
  - `scalar_sources(model_id: ColumnElement[Any], at_ms: ColumnElement[Any]) -> PriceSources` — looks every rate, rule and component up by correlated scalar subquery, effective-dated at `at_ms`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/unit/test_pricing.py`:

```python
from sqlalchemy import literal, select

from arhiteq_api.services.pricing import price_columns, scalar_sources


async def test_scalar_sources_price_a_model_the_same_way_the_view_does(session):
    """The two lookup strategies must be one implementation, not two.

    `model_price_select` reads the rate card as joined relations;
    `scalar_sources` reads it as correlated scalar subqueries so a call can be
    priced at its own start. If these ever disagree, the price endpoint and
    the margin dashboard describe the same minute differently.
    """
    await seed_pricing_defaults(session)
    at_ms = now_ms()

    from_view = {p.model_id: p for p in await model_prices(session, at_ms=at_ms)}

    for model_id, expected in from_view.items():
        cols = price_columns(scalar_sources(literal(model_id), literal(at_ms)))
        row = (
            await session.execute(
                select(
                    cols.cost_per_min_stack.label("cost"),
                    cols.price_per_min.label("price"),
                    cols.rule_source.label("rule_source"),
                )
            )
        ).mappings().one()

        assert row["cost"] == pytest.approx(expected.cost_per_min_stack)
        assert row["price"] == pytest.approx(expected.price_per_min)
        assert row["rule_source"] == expected.rule_source


async def test_scalar_sources_use_the_rate_in_force_at_that_instant(session):
    """A call is priced at its own start, not at the newest rate card."""
    session.add_all(
        [
            ModelCostRate(
                model_id="gemini-test-repriced",
                input_per_1m_usd=1.0,
                output_per_1m_usd=1.0,
                is_audio=True,
                effective_from_ms=0,
            ),
            ModelCostRate(
                model_id="gemini-test-repriced",
                input_per_1m_usd=10.0,
                output_per_1m_usd=10.0,
                is_audio=True,
                effective_from_ms=5_000,
            ),
        ]
    )
    await session.commit()

    def cost_at(at_ms: int) -> Any:
        cols = price_columns(scalar_sources(literal("gemini-test-repriced"), literal(at_ms)))
        return select(cols.cost_per_min_model.label("cost"))

    before = (await session.execute(cost_at(1_000))).scalar_one()
    after = (await session.execute(cost_at(9_000))).scalar_one()

    assert after == pytest.approx(before * 10)


async def test_scalar_sources_yield_null_for_a_model_with_no_rate(session):
    """Unknown must stay unknown: a 0 here would claim the model is free."""
    cols = price_columns(scalar_sources(literal("model-that-does-not-exist"), literal(now_ms())))

    row = (
        await session.execute(
            select(
                cols.cost_per_min_stack.label("cost"),
                cols.price_per_min.label("price"),
            )
        )
    ).mappings().one()

    assert row["cost"] is None
    assert row["price"] is None
```

Add whatever imports the module is missing (`Any`, `pytest`, `ModelCostRate`, `now_ms`, `model_prices`, `seed_pricing_defaults`) — match the existing style of that test file.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_pricing.py -v`
Expected: FAIL — `ImportError: cannot import name 'price_columns'`

- [ ] **Step 3: Extract the arithmetic**

In `backend/src/arhiteq_api/services/pricing.py`, add the two NamedTuples and `price_columns`, moving the body of `model_price_select` into it **unchanged** — same expressions, same order, same `case` chains:

```python
class PriceSources(NamedTuple):
    """Where the arithmetic in `price_columns` reads its inputs.

    Two implementations exist: `relation_sources`, which reads the rate card
    as joined relations (one row per model, the shape `pricing.model_price`
    wants), and `scalar_sources`, which reads it as correlated scalar
    subqueries so each row of an outer query -- a call -- can be priced at its
    own instant. Only the lookups differ; the formula below must not.
    """

    is_audio: ColumnElement[Any]
    input_per_1m: ColumnElement[Any]
    output_per_1m: ColumnElement[Any]
    component: Callable[[str], ColumnElement[Any]]
    model_rule: Callable[[Any], ColumnElement[Any]]
    global_rule: Callable[[Any], ColumnElement[Any]]


class PriceColumns(NamedTuple):
    """Unlabeled expressions. Callers label them to suit their select."""

    cost_per_min_model: ColumnElement[Any]
    cost_per_min_stack: ColumnElement[Any]
    price_per_min: ColumnElement[Any]
    rule_source: ColumnElement[Any]


def price_columns(sources: PriceSources) -> PriceColumns:
    """The one cost -> price formula. See the module docstring."""
    tokens_per_min = assumption("audio_tokens_per_sec") * 60.0
    talk = assumption("agent_talk_ratio")
    turns = assumption("turns_per_min")
    out_tokens = assumption("output_tokens_per_turn")
    in_tokens = assumption("display_input_tokens_per_turn")

    # No coalesce to 0: a renamed or expired component would then shave ~87%
    # off a text minute's cost with no signal at all. NULL propagates through
    # the sum, so a missing rate makes the whole stack cost unknown instead.
    stt = sources.component("cartesia_stt")
    tts = sources.component("cartesia_tts")

    # Audio models bill the audio stream; text models bill turns. Two formulas,
    # not two rates — applying the text formula to a Live model under-prices an
    # audio minute by roughly 6x.
    audio_cost = cast(
        tokens_per_min / 1e6 * sources.input_per_1m
        + talk * tokens_per_min / 1e6 * sources.output_per_1m,
        Float,
    )
    text_cost = cast(
        turns
        * (in_tokens / 1e6 * sources.input_per_1m + out_tokens / 1e6 * sources.output_per_1m),
        Float,
    )
    cost_model = case((sources.is_audio, audio_cost), else_=text_cost)
    # Live replaces STT+LLM+TTS with one model, so it has no synthesis leg —
    # and so a missing STT/TTS rate leaves an audio model's cost known.
    cost_stack = case((sources.is_audio, cost_model), else_=cost_model + stt + tts)

    def marked_up(markup: ColumnElement[Any], fixed: ColumnElement[Any]) -> ColumnElement[Any]:
        return cost_stack * (1.0 + func.coalesce(markup, 0.0) / 100.0) + func.coalesce(fixed, 0.0)

    model_explicit = sources.model_rule(PriceRule.explicit_per_minute_usd)
    model_markup = sources.model_rule(PriceRule.markup_pct)
    model_fixed = sources.model_rule(PriceRule.fixed_per_minute_usd)
    global_explicit = sources.global_rule(PriceRule.explicit_per_minute_usd)
    global_markup = sources.global_rule(PriceRule.markup_pct)
    global_fixed = sources.global_rule(PriceRule.fixed_per_minute_usd)

    # Within a scope: an explicit price wins, otherwise markup/fixed. A rule
    # row that sets none of the three is not a price — it is an empty row, so
    # the search continues to the next scope rather than pretending a 0%
    # markup was intended.
    model_has_derived = or_(model_markup.isnot(None), model_fixed.isnot(None))
    global_has_derived = or_(global_markup.isnot(None), global_fixed.isnot(None))

    # Falling through to `cost_stack` here is what silently sells at cost, so
    # the last branch is NULL. An operator who deletes the global rule gets a
    # blank price, which is visible; a price equal to cost is not.
    price = case(
        (model_explicit.isnot(None), model_explicit),
        (model_has_derived, marked_up(model_markup, model_fixed)),
        (global_explicit.isnot(None), global_explicit),
        (global_has_derived, marked_up(global_markup, global_fixed)),
        else_=null(),
    )
    rule_source = case(
        (model_explicit.isnot(None), literal("explicit")),
        (model_has_derived, literal("model")),
        # A flat platform price is "explicit" whichever scope carries it: the
        # signal a reader needs is that cost did not enter into it.
        (global_explicit.isnot(None), literal("explicit")),
        (global_has_derived, literal("global")),
        else_=literal("none"),
    )
    return PriceColumns(cost_model, cost_stack, price, rule_source)
```

Note the signature change to `model_rule`/`global_rule`: they now take an **ORM column** (`PriceRule.markup_pct`) rather than a relation column (`rules.c.markup_pct`), so both source builders can accept the same argument.

- [ ] **Step 4: Add the two source builders**

```python
def relation_sources(rates: Any, rules: Any, components: Any) -> PriceSources:
    """Read the rate card as joined relations: one row per model, priced at one
    instant. This is the shape `pricing.model_price` is built from."""

    def component(name: str) -> ColumnElement[Any]:
        return (
            select(components.c.unit_price_usd)
            .where(components.c.component == name)
            .scalar_subquery()
        )

    # A correlated scalar per column instead of one LATERAL join: LATERAL is
    # Postgres-only, and the tests — like any future SQLite consumer — must see
    # the same arithmetic the view will compute.
    def model_rule(column: Any) -> ColumnElement[Any]:
        return (
            select(rules.c[column.key])
            .where(rules.c.scope == rates.c.model_id)
            .correlate(rates)
            .scalar_subquery()
        )

    def global_rule(column: Any) -> ColumnElement[Any]:
        return select(rules.c[column.key]).where(rules.c.scope == "*").scalar_subquery()

    return PriceSources(
        is_audio=rates.c.is_audio,
        input_per_1m=rates.c.input_per_1m_usd,
        output_per_1m=rates.c.output_per_1m_usd,
        component=component,
        model_rule=model_rule,
        global_rule=global_rule,
    )


def _latest(column: Any, key_column: Any, key: Any, effective: Any, at_ms: Any) -> ColumnElement[Any]:
    """The value of `column` on the row in force at `at_ms`.

    ORDER BY ... LIMIT 1 inside a scalar subquery, which correlates to an
    outer row without LATERAL and renders identically on both dialects. This
    is the effective-dating `current_rows` does with a window function, in the
    shape a per-row lookup needs.
    """
    return (
        select(column)
        .where(key_column == key, effective <= at_ms)
        .order_by(effective.desc())
        .limit(1)
        .scalar_subquery()
    )


def scalar_sources(model_id: ColumnElement[Any], at_ms: ColumnElement[Any]) -> PriceSources:
    """Read the rate card by correlated lookup, effective-dated per outer row.

    `model_id` and `at_ms` are expressions from the calling query — typically
    a resolved model and a call's `start_timestamp` — so each call is priced
    with the rates and rules that were in force when it ran, rather than with
    today's rate card applied backwards over history.
    """
    return PriceSources(
        is_audio=_latest(
            ModelCostRate.is_audio, ModelCostRate.model_id, model_id,
            ModelCostRate.effective_from_ms, at_ms,
        ),
        input_per_1m=_latest(
            ModelCostRate.input_per_1m_usd, ModelCostRate.model_id, model_id,
            ModelCostRate.effective_from_ms, at_ms,
        ),
        output_per_1m=_latest(
            ModelCostRate.output_per_1m_usd, ModelCostRate.model_id, model_id,
            ModelCostRate.effective_from_ms, at_ms,
        ),
        component=lambda name: _latest(
            CostRate.unit_price_usd, CostRate.component, name,
            CostRate.effective_from_ms, at_ms,
        ),
        model_rule=lambda column: _latest(
            column, PriceRule.scope, model_id, PriceRule.effective_from_ms, at_ms
        ),
        global_rule=lambda column: _latest(
            column, PriceRule.scope, literal("*"), PriceRule.effective_from_ms, at_ms
        ),
    )
```

Add `Callable` to the `collections.abc` imports and `NamedTuple` is already imported.

- [ ] **Step 5: Rewrite `model_price_select` to use them**

Replace its body (keeping its docstring verbatim) with:

```python
    rates = current_rows(ModelCostRate, ModelCostRate.model_id, at_ms)
    rules = current_rows(PriceRule, PriceRule.scope, at_ms)
    components = current_rows(CostRate, CostRate.component, at_ms)
    cols = price_columns(relation_sources(rates, rules, components))

    return select(
        rates.c.model_id.label("model_id"),
        rates.c.is_audio.label("is_audio"),
        cols.cost_per_min_model.label("cost_per_min_model"),
        cols.cost_per_min_stack.label("cost_per_min_stack"),
        cols.price_per_min.label("price_per_min"),
        cols.rule_source.label("rule_source"),
        rates.c.input_per_1m_usd.label("input_per_1m_cost"),
        rates.c.output_per_1m_usd.label("output_per_1m_cost"),
    ).select_from(rates)
```

- [ ] **Step 6: Run the pricing tests**

Run: `cd backend && uv run pytest tests/unit/test_pricing.py tests/unit/test_pricing_view.py -v`
Expected: PASS — including the pre-existing assertions on the view's rendered SQL and the endpoint's numbers, which is the proof the refactor changed nothing.

- [ ] **Step 7: Run the full suite**

Run: `cd backend && uv run pytest -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/services/pricing.py backend/tests/unit/test_pricing.py
git commit -m "refactor(pricing): let one price formula read either rate-card shape"
```

---

## Task 6: `metrics.call_cost` — what each call cost and what it would have earned

**Files:**
- Modify: `backend/src/arhiteq_api/services/metrics_views.py`
- Modify: `backend/tests/unit/test_metrics_views.py`

**Interfaces:**
- Consumes: `pricing.price_columns`, `pricing.scalar_sources` (Task 5).
- Produces: `call_cost_select() -> Select[Any]` — columns `call_id, workspace_id, agent_id, day_ms, call_type, direction, model_id, minutes, cost_per_min, price_per_min, rule_source, variable_cost_usd, telephony_cost_usd, total_cost_usd, implied_price_usd, implied_margin_usd`.

**DEVIATION from the spec:** the spec resolves a call's model by joining `calls → agents → retell_llms`. That reads the agent's *current* configuration, so re-pointing an agent at a new model silently restates the cost of every call it ever took. `calls.agent_version` names the version that actually ran, and published versions are immutable snapshots, so the snapshot is preferred and the live row is the fallback for a draft. Flow-backed agents are resolved too — the spec's join path misses them entirely, and their cost would read NULL.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/unit/test_metrics_views.py`:

```python
async def test_call_cost_prices_a_call_from_the_version_that_ran(session):
    """An agent re-pointed at a new model must not restate old calls."""
    session.add(Workspace(id="ws_cost", name="Cost"))
    session.add(
        Agent(
            agent_id="ag_cost",
            workspace_id="ws_cost",
            response_engine={"llm_id": "llm_now"},
        )
    )
    session.add(
        RetellLLM(llm_id="llm_now", workspace_id="ws_cost", model="gemini-2.5-flash")
    )
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
    session.add(
        RetellLLM(llm_id="llm_trunk", workspace_id="ws_trunk", model="gemini-2.5-flash")
    )
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
```

Add `Agent`, `AgentVersion`, `RetellLLM` and `seed_pricing_defaults` to the test module's imports, and `call_cost_select` to the metrics import.

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: FAIL — `ImportError: cannot import name 'call_cost_select'`

- [ ] **Step 3: Implement the select**

Add `ConversationFlow`, `RetellLLM` to the model imports and `from .pricing import price_columns, scalar_sources`, then:

```python
def call_cost_select() -> Select[Any]:
    """One row per call: what it cost us and what it would have earned.

    "Would have earned": nobody is billed, so `implied_price_usd` is this
    call's minutes at the list price the pricing rule computes, not revenue.

    Cost is duration x rate, not metered consumption. `calls.call_cost` exists
    and has never been written, so a call that burned an unusual number of
    tokens costs the same here as a quiet one of equal length. This view is
    the seam where metered actuals replace the estimate later without any
    panel changing.
    """
    at_ms = func.coalesce(Call.start_timestamp, Call.created_at_ms)

    # Which model ran, most authoritative first. A published version is an
    # immutable snapshot of what actually ran; the live rows are the fallback
    # for a draft, whose content *is* the live row (docs/AGENT_VERSIONING.md).
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

    sources = scalar_sources(model_id, at_ms)
    cols = price_columns(sources)

    minutes = cast(
        case((Call.duration_ms > 0, Call.duration_ms / _MS_PER_MINUTE), else_=0.0), Float
    )
    # 0.0 for a web call is a real zero, not an unknown: no trunk was used.
    telephony_per_min = case(
        (Call.call_type != "phone_call", literal(0.0)),
        (Call.direction == "inbound", sources.component("telnyx_inbound")),
        (Call.direction == "outbound", sources.component("telnyx_outbound")),
        else_=null(),
    )
    variable_cost = minutes * cols.cost_per_min_stack
    telephony_cost = minutes * telephony_per_min
    implied_price = minutes * cols.price_per_min

    return (
        select(
            Call.call_id.label("call_id"),
            Call.workspace_id.label("workspace_id"),
            Call.agent_id.label("agent_id"),
            _bucket(at_ms, _MS_PER_DAY).label("day_ms"),
            Call.call_type.label("call_type"),
            Call.direction.label("direction"),
            model_id.label("model_id"),
            minutes.label("minutes"),
            cols.cost_per_min_stack.label("cost_per_min"),
            cols.price_per_min.label("price_per_min"),
            cols.rule_source.label("rule_source"),
            variable_cost.label("variable_cost_usd"),
            telephony_cost.label("telephony_cost_usd"),
            (variable_cost + telephony_cost).label("total_cost_usd"),
            implied_price.label("implied_price_usd"),
            (implied_price - (variable_cost + telephony_cost)).label("implied_margin_usd"),
        )
        .select_from(Call)
        .outerjoin(Agent, Agent.agent_id == Call.agent_id)
    )
```

Register it in `VIEWS` after `workspace_daily`.

- [ ] **Step 4: Run to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_metrics_views.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Run the full suite and lint**

Run: `cd backend && uv run pytest -q && cd .. && pre-commit run --files backend/src/arhiteq_api/services/metrics_views.py backend/src/arhiteq_api/services/pricing.py backend/src/arhiteq_api/services/view_ddl.py backend/tests/unit/test_metrics_views.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/arhiteq_api/services/metrics_views.py backend/tests/unit/test_metrics_views.py
git commit -m "feat(api): add the call_cost metrics view"
```

---

## Task 7: `grafana_ro` — a role that can read the answers and not the transcripts

**Files:**
- Create: `infra/sql/grafana_ro.sql`
- Modify: `infra/README.md`

**Interfaces:** none in code. This is operator SQL plus its runbook.

**The trap this task exists to avoid:** `apply_views` drops and recreates every view on **every API boot**, and Postgres drops a view's grants along with the view. A plain `GRANT SELECT ON metrics.workspace_daily TO grafana_ro` therefore survives until the next deploy and then silently stops working — the dashboard breaks hours later, with no deploy to blame it on. `ALTER DEFAULT PRIVILEGES FOR ROLE arhiteq IN SCHEMA metrics` is what makes the grant reattach to each recreated view. The same applies to `pricing.model_price`, which has been dropped and recreated on every boot since #284.

- [ ] **Step 1: Write the script**

Create `infra/sql/grafana_ro.sql`:

```sql
-- Grafana's read-only role. Run once per database, as a superuser (the
-- `postgres` user on Cloud SQL), from infra/README.md § Grafana database access.
--
-- The application deliberately cannot do this: creating roles and granting
-- privileges is exactly the authority a compromised API should not hold.
--
-- What this grants, and what it very deliberately does not: `grafana_ro` can
-- read the four `metrics` views and the `pricing.model_price` view, and holds
-- no privilege on any base table. That is what makes call transcripts
-- unreachable rather than merely unselected -- `calls.transcript` and
-- `calls.transcript_object` hold customer conversation content, and a role
-- with SELECT on `calls` is one ad-hoc query away from reading all of it.
-- Views in Postgres 16 default to security_invoker = off, so they execute
-- with their owner's privileges: the view reaches the base table and the
-- caller does not. Leaving that default in place is load-bearing.

\set ON_ERROR_STOP on

-- Password comes from the environment so it never lands in this file or in
-- shell history: psql -v pw="$GRAFANA_DB_PASSWORD" -f infra/sql/grafana_ro.sql
CREATE ROLE grafana_ro LOGIN PASSWORD :'pw';

GRANT CONNECT ON DATABASE arhiteq TO grafana_ro;
GRANT USAGE ON SCHEMA metrics, pricing TO grafana_ro;

-- The views that exist right now.
GRANT SELECT ON ALL TABLES IN SCHEMA metrics TO grafana_ro;
GRANT SELECT ON pricing.model_price TO grafana_ro;

-- ...and the ones the next API boot will replace them with. The API installs
-- these views by DROP-then-CREATE on every boot (services/view_ddl.py), which
-- takes each view's grants down with it. Without these two statements the
-- dashboard works until the next deploy and then breaks with no deploy in the
-- blast radius. FOR ROLE arhiteq because default privileges apply to objects
-- created by a named role, and the API connects as `arhiteq`.
ALTER DEFAULT PRIVILEGES FOR ROLE arhiteq IN SCHEMA metrics
  GRANT SELECT ON TABLES TO grafana_ro;
ALTER DEFAULT PRIVILEGES FOR ROLE arhiteq IN SCHEMA pricing
  GRANT SELECT ON TABLES TO grafana_ro;

-- A runaway panel must not be able to sit on the production database.
ALTER ROLE grafana_ro SET statement_timeout = '30s';
```

- [ ] **Step 2: Document the runbook**

In `infra/README.md`, under § Grafana access, add a subsection. Include the verification, because a grant model nobody checked is an intention rather than a property:

````markdown
### Grafana database access

The business-metrics dashboard reads Cloud SQL directly through a dedicated
role. Create it once per database, as a superuser:

```bash
# Cloud SQL has no superuser shell; connect through the proxy as `postgres`.
cloud-sql-proxy usan-retirement:us-east1:arhiteq-pg &
psql "host=127.0.0.1 user=postgres dbname=arhiteq" \
  -v pw="$GRAFANA_DB_PASSWORD" -f infra/sql/grafana_ro.sql
```

Then verify the isolation actually holds — the point of the role is what it
*cannot* do:

```bash
psql "host=127.0.0.1 user=grafana_ro dbname=arhiteq" -c 'SELECT count(*) FROM metrics.workspace_daily'
# -> a number
psql "host=127.0.0.1 user=grafana_ro dbname=arhiteq" -c 'SELECT transcript FROM calls LIMIT 1'
# -> ERROR: permission denied for table calls
```

Re-run the second check after any deploy that changes the views: the API
recreates them on every boot, and the `ALTER DEFAULT PRIVILEGES` lines in the
script are the only reason the grants come back. If they were skipped, the
first query starts failing after a deploy rather than at setup time.

Set `GRAFANA_DB_PASSWORD` in `infra/private/prod.env` before running
`gen-values.sh`; the datasource reads it from a Secret.
````

- [ ] **Step 3: Commit**

```bash
git add infra/sql/grafana_ro.sql infra/README.md
git commit -m "docs(infra): add the grafana_ro role script and runbook"
```

---

## Task 8: The Postgres datasource

**Files:**
- Modify: `infra/helm/monitoring/values.yaml`
- Modify: `infra/helm/monitoring/gen-values.sh`

- [ ] **Step 1: Add the datasource to `values.yaml`**

Under `grafana:`, next to the existing blocks, add:

```yaml
  # Business metrics read Cloud SQL directly (see docs/superpowers/specs/
  # 2026-08-20-business-metrics-dashboard-design.md). No exporter and no
  # proxy: the Grafana pod already sits in the VPC that reaches the private
  # IP, and the cluster has no NetworkPolicies to traverse.
  additionalDataSources:
    - name: Arhiteq DB
      # Pinned, because dashboards reference it by uid: file provisioning
      # never resolves a ${DS_*} placeholder, so a generated uid would make
      # every business panel read "Datasource not found".
      uid: arhiteq-db
      type: postgres
      url: 10.145.0.2:5432
      database: arhiteq
      user: grafana_ro
      jsonData:
        sslmode: require
        postgresVersion: 1600
        # The role carries statement_timeout = '30s' server-side; this is the
        # client half, so a hung panel gives up rather than holding a
        # connection until the pool starves.
        timescaledb: false
      secureJsonData:
        # Never in values.yaml: Helm stores the rendered values in the release,
        # where `helm get values monitoring` would print it back out.
        password: ${GRAFANA_DB_PASSWORD}
```

`secureJsonData` referencing `${GRAFANA_DB_PASSWORD}` works because the chart's datasource provisioning expands environment variables. Add the env var to the Grafana pod in the same file's `grafana.envValueFrom` (or `extraSecretMounts`-adjacent `env` block — match whatever the OAuth client already uses):

```yaml
    GRAFANA_DB_PASSWORD:
      secretKeyRef:
        name: grafana-db
        key: password
```

Fold the password into the existing pod checksum annotation so rotating it rolls Grafana, rather than leaving the old credential live in a running process — replace the OAuth checksum placeholder with a combined one:

```yaml
      annotations:
        checksum/credentials: CHANGE_ME_GRAFANA_CREDENTIALS_CHECKSUM
```

- [ ] **Step 2: Apply the Secret and render the placeholder in `gen-values.sh`**

Add the requirement next to the other `: "${...:?}"` lines:

```bash
: "${GRAFANA_DB_PASSWORD:?set it in infra/private/prod.env — see infra/README.md § Grafana database access}"
```

Add the Secret next to `grafana-google-oauth`:

```bash
# Same pattern as the OAuth client: a Secret rather than a value, so the
# credential stays out of the Helm release.
kubectl -n monitoring create secret generic grafana-db \
  --from-literal=password="$GRAFANA_DB_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -
```

In the Python renderer, replace the `CHANGE_ME_GRAFANA_OAUTH_CHECKSUM` substitution with one covering all three credentials, and keep the placeholder name in sync with `values.yaml`:

```python
    # Not the credentials themselves — this annotation is world-readable to
    # anyone who can get the pod. All three are folded in together so rotating
    # any one of them rolls Grafana; a pod that keeps running holds the old
    # credential in memory however the Secret looks.
    "CHANGE_ME_GRAFANA_CREDENTIALS_CHECKSUM": hashlib.sha256(
        "\0".join(
            (
                os.environ["GRAFANA_OAUTH_CLIENT_ID"],
                os.environ["GRAFANA_OAUTH_CLIENT_SECRET"],
                os.environ["GRAFANA_DB_PASSWORD"],
            )
        ).encode()
    ).hexdigest(),
```

The renderer already fails on a placeholder it cannot find, so a rename that misses one side is caught at render time rather than at deploy time.

- [ ] **Step 3: Render without deploying, to check the substitution**

Run:
```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
GRAFANA_DB_PASSWORD=render-check bash -n infra/helm/monitoring/gen-values.sh && echo "syntax ok"
```
Expected: `syntax ok`. (The full run happens in Task 10; this only proves the script parses.)

- [ ] **Step 4: Commit**

```bash
git add infra/helm/monitoring/values.yaml infra/helm/monitoring/gen-values.sh
git commit -m "feat(infra): give Grafana a read-only Cloud SQL datasource"
```

---

## Task 9: The dashboard

Four rows, each panel titled with the question it answers. Every panel pins `"datasource": {"type": "postgres", "uid": "arhiteq-db"}`, and the file carries no `__inputs` block.

**Files:**
- Create: `infra/helm/monitoring/dashboards/arhiteq-business.json`

- [ ] **Step 1: Write the dashboard**

Create `infra/helm/monitoring/dashboards/arhiteq-business.json` with `"uid": "arhiteq-business"`, `"title": "Arhiteq — Business"`, `"tags": ["arhiteq", "business"]`, `"schemaVersion": 39`, `"time": {"from": "now-30d", "to": "now"}`, `"refresh": "5m"`, and these panels. Match the structure of `arhiteq-calls.json` (id, type, title, datasource, gridPos, fieldConfig, targets) — the Postgres targets use `"format"` and `"rawSql"` instead of `"expr"`:

**Row 1 — Adoption** (`stat` panels, `"format": "table"`):

```sql
-- Workspaces / Members / Agents / Phone numbers: one tile each,
-- entity swapped per panel.
SELECT count(*) AS "Workspaces" FROM metrics.tenancy WHERE entity = 'workspace'
```

**Row 2 — Load** (`timeseries`, `"format": "time_series"`):

```sql
-- Calls per day by direction
SELECT to_timestamp(day_ms / 1000) AS time,
       sum(inbound_calls) AS "inbound",
       sum(outbound_calls) AS "outbound"
FROM metrics.workspace_daily
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY day_ms ORDER BY day_ms
```

```sql
-- Minutes per day
SELECT to_timestamp(day_ms / 1000) AS time, sum(minutes) AS "minutes"
FROM metrics.workspace_daily
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY day_ms ORDER BY day_ms
```

```sql
-- Peak concurrent calls per hour
SELECT to_timestamp(hour_ms / 1000) AS time, max(peak_concurrent) AS "peak"
FROM metrics.concurrency_hourly
WHERE hour_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY hour_ms ORDER BY hour_ms
```

```sql
-- Calls that never connected, per day. Titled "Calls that never connected
-- (excluded from minutes)" so the gap between calls and minutes is explained
-- on screen rather than looking like a bug.
SELECT to_timestamp(day_ms / 1000) AS time,
       sum(unconnected_calls) AS "never connected",
       sum(error_calls) AS "errored"
FROM metrics.workspace_daily
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY day_ms ORDER BY day_ms
```

**Row 3 — Model usage.** Both panels carry the coverage in their `description`, and the second one reports it as a number, since turn data begins at #261 rather than at the start of history:

```sql
-- LLM turns per day
SELECT to_timestamp(day_ms / 1000) AS time, sum(llm_turns) AS "turns"
FROM metrics.workspace_daily
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY day_ms ORDER BY day_ms
```

```sql
-- Turn-data coverage: "N of M calls". Panel title says so.
SELECT sum(calls_with_turns) AS "calls with turn data", sum(calls) AS "calls"
FROM metrics.workspace_daily
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
```

**Row 4 — Economics:**

```sql
-- Cost per minute (headline stat). NULL-safe: minutes with unknown cost are
-- excluded from both halves rather than counted as free.
SELECT sum(total_cost_usd) / nullif(sum(CASE WHEN total_cost_usd IS NOT NULL THEN minutes END), 0)
       AS "cost/min"
FROM metrics.call_cost
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
```

```sql
-- Cost and implied margin per day
SELECT to_timestamp(day_ms / 1000) AS time,
       sum(total_cost_usd) AS "cost",
       sum(implied_margin_usd) AS "implied margin"
FROM metrics.call_cost
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY day_ms ORDER BY day_ms
```

```sql
-- Per workspace (table). Fixed infrastructure is allocated by minute share and
-- kept in its own column: folding a fixed cost into a per-minute one produces a
-- number that falls as volume rises and looks like an efficiency gain that is
-- really just division.
WITH window_calls AS (
  SELECT * FROM metrics.call_cost
  WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
), totals AS (
  SELECT sum(minutes) AS all_minutes FROM window_calls
), infra AS (
  SELECT unit_price_usd
         * ((($__unixEpochTo() - $__unixEpochFrom())::numeric) / 86400 / 30) AS window_usd
  FROM cost_rates WHERE component = 'infra_fixed_monthly'
    AND effective_from_ms <= $__unixEpochTo() * 1000
  ORDER BY effective_from_ms DESC LIMIT 1
)
SELECT c.workspace_id AS "workspace",
       count(*) AS "calls",
       round(sum(c.minutes)::numeric, 1) AS "minutes",
       round(sum(c.total_cost_usd)::numeric, 4) AS "variable cost",
       round((i.window_usd * sum(c.minutes) / nullif(t.all_minutes, 0))::numeric, 2)
         AS "allocated infra",
       round((sum(c.total_cost_usd) / nullif(sum(c.minutes), 0))::numeric, 5) AS "cost/min",
       round(sum(c.implied_margin_usd)::numeric, 4) AS "implied margin"
FROM window_calls c CROSS JOIN totals t CROSS JOIN infra i
GROUP BY c.workspace_id, i.window_usd, t.all_minutes
ORDER BY sum(c.minutes) DESC
```

```sql
-- Spread by model, reading rule_source so an oddly-priced model shows which
-- rule produced it rather than leaving you to reconstruct it from three tables.
SELECT model_id AS "model",
       rule_source AS "priced by",
       count(*) AS "calls",
       round(sum(minutes)::numeric, 1) AS "minutes",
       round(avg(cost_per_min)::numeric, 5) AS "cost/min",
       round(avg(price_per_min)::numeric, 5) AS "price/min",
       round(sum(implied_margin_usd)::numeric, 4) AS "implied margin"
FROM metrics.call_cost
WHERE day_ms BETWEEN $__unixEpochFrom() * 1000 AND $__unixEpochTo() * 1000
GROUP BY model_id, rule_source
ORDER BY sum(minutes) DESC
```

Set the economics row's panel `description` fields to state that margin is **implied** — what this traffic would have earned at list price — because nobody is billed.

- [ ] **Step 2: Validate the JSON and the invariants**

Run:
```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("infra/helm/monitoring/dashboards/arhiteq-business.json")
d = json.loads(p.read_text())
assert "__inputs" not in d, "an exported __inputs block would break file provisioning"
assert d["uid"] == "arhiteq-business"
for panel in d["panels"]:
    assert panel["datasource"] == {"type": "postgres", "uid": "arhiteq-db"}, panel["title"]
    for target in panel["targets"]:
        assert "${DS_" not in target["rawSql"]
print(f"{len(d['panels'])} panels ok")
PY
```
Expected: `N panels ok`

- [ ] **Step 3: Commit**

```bash
git add infra/helm/monitoring/dashboards/arhiteq-business.json
git commit -m "feat(infra): add the business metrics dashboard"
```

---

## Task 10: Roll out and verify against prod

Steps 1 and 2 need privileges the platform does not hold; they are manual by design.

- [ ] **Step 1: Open the PR and let it land**

```bash
git push -u origin feat/business-metrics-dashboard
gh pr create --title "feat(metrics): add the business metrics dashboard" --body "..."
```

`main` is protected: CI must be green and the PR squash-merged. The API change ships with the next release, and boot creates the schema and views.

- [ ] **Step 2: Create the role (operator)**

Follow `infra/README.md` § Grafana database access. Run both verification queries; the second must fail with `permission denied for table calls`.

- [ ] **Step 3: Set the password and deploy monitoring (operator)**

Add `GRAFANA_DB_PASSWORD` to `infra/private/prod.env`, then:

```bash
infra/helm/monitoring/gen-values.sh
```

Datasource, dashboard and Secret land together.

- [ ] **Step 4: Verify each panel against the database**

For every panel, run its `rawSql` directly against Cloud SQL as `grafana_ro` and compare with what the panel draws — the same way the Prometheus panels were validated in #283. Check specifically that:
- `metrics.tenancy` counts match `SELECT count(*)` on each base table.
- Total minutes match `SELECT sum(duration_ms)/60000.0 FROM calls WHERE duration_ms > 0`.
- The workspace table's `calls` column sums to the row count of `metrics.call_cost` in the window.
- No economics cell reads `0` where the model has no rate row — it must read blank.

- [ ] **Step 5: Confirm the grants survive a view recreate**

This is the failure mode `ALTER DEFAULT PRIVILEGES` exists to prevent, and the only honest way to know it worked is to force the recreate:

```bash
kubectl -n arhiteq rollout restart deployment/arhiteq-api
kubectl -n arhiteq rollout status deployment/arhiteq-api
psql "host=127.0.0.1 user=grafana_ro dbname=arhiteq" -c 'SELECT count(*) FROM metrics.call_cost'
```
Expected: a number, not `permission denied`.

- [ ] **Step 6: Re-read the seeded rate card (operator)**

The figures in `services/pricing_seed.py` were read on 2026-08-20 and provider prices move. The `note` column on each row says where to look. This is a judgement call, not a deploy step — but the dashboard's cost numbers are only as good as it.

---

## Self-Review

**Spec coverage.** Every section maps to a task: architecture and DDL delivery → Tasks 1–2; the four views → Tasks 2, 3, 4, 6; cost and margin → Tasks 5–6; security model → Task 7; datasource → Task 8; panels and the three honesty rules → Task 9 (rule 1 in the "never connected" panel and `unconnected_calls`, rule 2 in the NULL tests of Tasks 5–6 and the `nullif` in the economics SQL, rule 3 in the coverage panel and `calls_with_turns`); verification → Tasks 2–6 unit tests, Task 7 grant check, Task 10 prod comparison; rollout → Task 10.

**Deviations, all deliberate and marked in place:** (1) `tenancy` dates agents from their first version and leaves phone numbers NULL, because `agents` and `phone_numbers` record no creation time; (2) `call_cost` resolves the model from the version that ran and handles flow-backed agents, not from the agent's current config; (3) the price is evaluated at each call's start through a shared formula rather than joined from `pricing.model_price`, which has no time dimension.

**One gap the spec did not anticipate,** covered in Task 7: boot recreates every view, which drops its grants, so the security model needs `ALTER DEFAULT PRIVILEGES` or the dashboard breaks on the deploy after setup.


---

## What the build changed about this plan

Recorded after execution, because a plan that does not match what shipped is
worse than no plan.

1. **A fifth view, `metrics.fixed_cost`.** The per-workspace panel read
   `cost_rates` directly for the infra allocation — a table `grafana_ro` holds
   no grant on, and must not, since it also carries the per-minute rates the
   margin is derived from. Caught by running the panels *as `grafana_ro`*
   against a real Postgres; the panel now reads the view.

2. **`::bigint` on every `$__unixEpoch*()` macro.** Grafana substitutes them as
   bare numeric literals, which Postgres types as `int4`; `epoch_seconds *
   1000` overflows it and every panel dies with "integer out of range". Not
   reachable from the SQLite suite — only from executing the panel SQL.

3. **`_bucket` uses floor division (`//`), not `/`.** SQLAlchemy 2.0 renders
   `/` as true division, so `ts / 86400000 * 86400000` came back a hair under
   the boundary (`863999999.9999999`) and every bucket edge became its own
   group.

4. **`call_cost` is built in three layers.** Inlining the resolved model into
   `scalar_sources` put a four-way coalesce of subqueries inside all ~20 rate
   lookups the price formula expands to: the compiled view was **168KB** of
   SQL. Resolving the model in one layer and the cost in another brought it to
   **17KB**, and `price_columns` was split into `cost_columns` +
   `price_from_cost` so the second layer could pass a column where the formula
   wanted an expression.

5. **`apply_views` takes Selects, not SQL strings.** Rendering before the
   dialect check made every SQLite boot in the suite compile four views and
   throw them away.

6. **The DDL-behaviour tests moved** from `test_pricing_view.py` to
   `test_view_ddl.py`, following the code they cover; `test_pricing_view.py`
   keeps the rendering tests and gains one that pins which view goes in which
   schema.

## Verified before merge

Against a throwaway `postgres:16` container, since neither the SQLite suite nor
CI can reach any of this:

- All five views install through the real boot path, and again on a simulated
  second boot.
- Every panel's SQL executes as `grafana_ro` with zero errors, and the numbers
  reconcile by hand: a 2-minute Live call costs `2 x $0.0135` plus `2 x
  $0.0032` inbound trunk, prices at 4x cost under the seeded 300% markup, and
  a web call of equal length pays no trunk at all.
- `grafana_ro` is refused on `calls`, `workspaces`, `cost_rates` and
  `price_rules`, and carries `statement_timeout = 30s`.
- **The grant survives a view recreate:** `metrics.fixed_cost` was created
  *after* `grafana_ro.sql` ran and never received an explicit `GRANT`, and
  `grafana_ro` reads it — which is `ALTER DEFAULT PRIVILEGES` doing the job the
  spec's plain `GRANT` would have failed at on the first deploy.
