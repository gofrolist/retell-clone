import logging

import pytest
from sqlalchemy.exc import DBAPIError

from arhiteq_api.db import session_factory
from arhiteq_api.services import pricing_view
from arhiteq_api.services.pricing_seed import seed_pricing_defaults
from arhiteq_api.services.pricing_view import apply_pricing_view, render_pricing_view_sql


async def test_skipped_on_sqlite():
    """SQLite has no schemas; the suite must not need a Postgres to run."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        assert await apply_pricing_view(session) is False


def test_rendered_sql_is_a_plain_create_view_with_no_bind_params():
    """A view cannot carry parameters, so nothing may render as a placeholder.

    Plain CREATE VIEW rather than CREATE OR REPLACE: `apply_pricing_view`
    drops the view first, which is what keeps a future change to the select's
    column shape from crashlooping every API replica at boot (42P16).
    """
    sql = render_pricing_view_sql()

    assert sql.startswith("CREATE VIEW pricing.model_price AS")
    assert "CREATE OR REPLACE" not in sql
    assert "%(" not in sql and "?" not in sql
    assert "price_per_min" in sql


def test_rendered_sql_reads_the_assumptions_per_query_instead_of_freezing_them():
    """An assumption edit must reach Grafana when it reaches the API.

    The assumptions used to be loaded into Python and folded into the
    arithmetic, so the view carried `audio_tokens_per_sec(25) * 60 / 1e6` as
    the literal coefficient 0.0015 and only picked up an edit the next time
    the API restarted — until then the price endpoint and the margin panel
    described the same call differently. They must render as subqueries
    against pricing_assumptions instead, exactly like the now()-derived
    timestamp below.
    """
    sql = render_pricing_view_sql()

    assert "pricing_assumptions" in sql
    assert "0.0015" not in sql, "an assumption was folded into a literal coefficient"
    # The keys are what the subqueries select on, so they must appear by name.
    for key in ("audio_tokens_per_sec", "agent_talk_ratio", "turns_per_min"):
        assert key in sql


def test_rendered_sql_reevaluates_time_per_query_instead_of_freezing_it():
    """The view must price against "now" on every query, not against the

    timestamp that happened to be current when the view was (re)created — a
    frozen rate would mean a provider reprice never reaches the dashboard
    until someone redeploys. So the timestamp comparison must render as a
    Postgres now()-derived expression, never as a fixed integer millisecond
    literal that `literal_binds=True` would otherwise inline.
    """
    sql = render_pricing_view_sql()

    assert "now()" in sql.lower()


class _FakeBind:
    class dialect:
        name = "postgresql"


# The DDL apply_pricing_view issues, in order. The 1-based index of each is
# what `_FakeSession` fails on, so a race test can pick which statement loses.
LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL = 1, 2, 3, 4


class _FakeSession:
    """Just enough of AsyncSession's surface for apply_pricing_view: four
    session.execute() calls (SET LOCAL lock_timeout, CREATE SCHEMA, DROP VIEW,
    CREATE VIEW), then commit() on success or rollback() on a caught race or
    lock timeout.

    SQLite -- what the rest of this suite runs on -- has no schemas, no
    Postgres SQLSTATEs, and (per test_skipped_on_sqlite) apply_pricing_view
    no-ops on it before reaching any of this logic. A genuine concurrent
    race is also inherently racy to trigger on demand. So the only way to
    exercise the exception-handling branch deterministically in the pytest
    suite is to simulate the DBAPIError a losing replica gets from Postgres,
    the way this fake does. Real concurrency was exercised separately,
    manually, against a throwaway postgres:18 container -- see the PR/task
    report for that run's output; it is not part of this automated suite
    because the suite has no Postgres available.
    """

    def __init__(self, error: Exception | None = None, fail_on: int = CREATE_CALL):
        self._error = error
        self._fail_on = fail_on
        self.calls: list[str] = []
        self.rolled_back = False
        self.committed = False

    def get_bind(self):
        return _FakeBind()

    async def execute(self, stmt):
        self.calls.append(str(stmt))
        if len(self.calls) == self._fail_on and self._error is not None:
            raise self._error

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _fake_dbapi_error(sqlstate: str) -> DBAPIError:
    class _Orig(Exception):
        pass

    orig = _Orig("simulated driver error")
    orig.sqlstate = sqlstate
    return DBAPIError("CREATE VIEW pricing.model_price AS ...", None, orig)


@pytest.fixture
def _stub_render(monkeypatch):
    monkeypatch.setattr(
        pricing_view,
        "render_pricing_view_sql",
        lambda: "CREATE VIEW pricing.model_price AS SELECT 1",
    )


async def test_apply_drops_the_view_before_creating_it(_stub_render):
    """The crashloop this ordering exists to prevent.

    Postgres refuses CREATE OR REPLACE VIEW when the select's column names,
    types or order change (42P16), so the first rollout carrying such a change
    would fail in the lifespan and take every API replica down with it -- for
    a view only Grafana reads. Dropping first makes a shape change a normal
    deploy.
    """
    session = _FakeSession()
    assert await apply_pricing_view(session) is True

    assert session.committed is True
    assert len(session.calls) == 4
    assert session.calls[LOCK_CALL - 1] == pricing_view._SET_LOCK_TIMEOUT_SQL
    assert session.calls[SCHEMA_CALL - 1] == "CREATE SCHEMA IF NOT EXISTS pricing"
    assert session.calls[DROP_CALL - 1] == "DROP VIEW IF EXISTS pricing.model_price"
    assert session.calls[CREATE_CALL - 1].startswith("CREATE VIEW pricing.model_price")


@pytest.mark.parametrize("sqlstate", sorted(pricing_view._RACE_SQLSTATES))
@pytest.mark.parametrize("fail_on", [SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_swallows_a_known_concurrent_ddl_race(_stub_render, sqlstate, fail_on):
    """Any of the three boot statements can lose a race with another replica's
    identical DDL and raise one of a known set of SQLSTATEs rather than
    failing cleanly -- Postgres's existence checks are not isolated from
    concurrent DDL (reproduced for real: four replicas booting at once against
    Postgres 18 hit 23505 unique_violation on the pg_namespace/pg_type system
    catalog indexes). Parametrised over the statement that loses, because the
    handling has to cover the DROP and the CREATE, not just whichever one
    happens to be second. apply_pricing_view must treat every SQLSTATE in
    _RACE_SQLSTATES as success -- the winner already put the schema/view in
    the state this replica wanted, so boot must proceed rather than crash the
    pod -- and must leave the session usable rather than poisoned.
    """
    session = _FakeSession(_fake_dbapi_error(sqlstate), fail_on=fail_on)
    assert await apply_pricing_view(session) is True
    assert session.rolled_back is True, "a caught race must roll back the poisoned transaction"
    assert session.committed is False


async def test_apply_sets_a_short_lock_timeout_before_the_ddl(_stub_render):
    """Without a bound, a Grafana query holding an ACCESS SHARE lock on the
    view blocks DROP VIEW indefinitely -- and with it the FastAPI lifespan,
    so the pod never becomes ready. SET LOCAL scopes the timeout to this
    transaction only, so it cannot leak onto a pooled connection's later,
    unrelated queries.
    """
    session = _FakeSession()
    assert await apply_pricing_view(session) is True

    assert session.calls[LOCK_CALL - 1] == "SET LOCAL lock_timeout = '5000ms'"
    # It must run before any of the DDL it is meant to bound.
    assert session.calls[LOCK_CALL - 1] == session.calls[0]


@pytest.mark.parametrize("fail_on", [LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_swallows_a_lock_timeout_and_keeps_the_previous_view(
    _stub_render, fail_on, caplog
):
    """A losing wait for that lock_timeout (55P03 lock_not_available) must not
    fail the lifespan: the view already exists from a previous boot and is
    still serving Grafana, so a blocked refresh is logged and treated as
    success rather than propagated -- unlike a genuine SQL error below, which
    still must fail loudly. Parametrised over every statement in the
    transaction, because any of them can be the one waiting on the lock.
    """
    session = _FakeSession(_fake_dbapi_error(pricing_view._LOCK_TIMEOUT_SQLSTATE), fail_on=fail_on)
    with caplog.at_level(logging.WARNING):
        assert await apply_pricing_view(session) is True

    assert session.rolled_back is True, "a caught timeout must roll back the poisoned transaction"
    assert session.committed is False
    assert "lock" in caplog.text.lower()
    assert "pricing.model_price" in caplog.text


@pytest.mark.parametrize("fail_on", [LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_re_raises_a_genuine_ddl_error(_stub_render, fail_on):
    """A real bug in the generated SQL -- a syntax error (42601 syntax_error)
    is the archetype now that a column-shape change can no longer produce
    42P16 -- must still fail loudly at boot, whichever statement raises it: a
    silently-missing view means the Grafana margin dashboard reads stale or
    absent prices with no signal.
    """
    session = _FakeSession(_fake_dbapi_error("42601"), fail_on=fail_on)
    with pytest.raises(DBAPIError):
        await apply_pricing_view(session)
