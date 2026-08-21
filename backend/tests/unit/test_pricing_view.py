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


async def test_rendered_sql_is_a_create_or_replace_view_with_no_bind_params():
    """A view cannot carry parameters — every assumption must be inlined."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        sql = await render_pricing_view_sql(session)

    assert sql.startswith("CREATE OR REPLACE VIEW pricing.model_price AS")
    assert "%(" not in sql and "?" not in sql
    assert "price_per_min" in sql


async def test_rendered_sql_inlines_the_assumption_values():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        sql = await render_pricing_view_sql(session)

    # model_price_select folds `audio_tokens_per_sec(25) * 60 / 1e6` in Python
    # before it becomes a literal, so the coefficient itself — not the raw
    # 1500.0 tokens/min — is what shows up inlined in the view body.
    assert "0.0015" in sql


async def test_rendered_sql_reevaluates_time_per_query_instead_of_freezing_it():
    """The view must price against "now" on every query, not against the

    timestamp that happened to be current when the view was (re)created — a
    frozen rate would mean a provider reprice never reaches the dashboard
    until someone redeploys. So the timestamp comparison must render as a
    Postgres now()-derived expression, never as a fixed integer millisecond
    literal that `literal_binds=True` would otherwise inline.
    """
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        sql = await render_pricing_view_sql(session)

    assert "now()" in sql.lower()


class _FakeBind:
    class dialect:
        name = "postgresql"


class _FakeSession:
    """Just enough of AsyncSession's surface for apply_pricing_view: two
    session.execute() calls (CREATE SCHEMA, then CREATE OR REPLACE VIEW),
    then commit() on success or rollback() on a caught race.

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

    def __init__(self, second_call_error: Exception | None):
        self._second_call_error = second_call_error
        self.calls: list[str] = []
        self.rolled_back = False
        self.committed = False

    def get_bind(self):
        return _FakeBind()

    async def execute(self, stmt):
        self.calls.append(str(stmt))
        if len(self.calls) == 2 and self._second_call_error is not None:
            raise self._second_call_error

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


def _fake_dbapi_error(sqlstate: str) -> DBAPIError:
    class _Orig(Exception):
        pass

    orig = _Orig("simulated driver error")
    orig.sqlstate = sqlstate
    return DBAPIError("CREATE OR REPLACE VIEW pricing.model_price AS ...", None, orig)


@pytest.mark.parametrize("sqlstate", sorted(pricing_view._RACE_SQLSTATES))
async def test_apply_swallows_a_known_concurrent_ddl_race(monkeypatch, sqlstate):
    """A losing replica's CREATE SCHEMA IF NOT EXISTS / CREATE OR REPLACE
    VIEW can collide with another replica's identical DDL and raise one of
    a known set of SQLSTATEs rather than failing cleanly -- Postgres's
    existence checks are not isolated from concurrent DDL (reproduced for
    real: four replicas booting at once against Postgres 18 hit 23505
    unique_violation on the pg_namespace/pg_type system catalog indexes).
    apply_pricing_view must treat every SQLSTATE in _RACE_SQLSTATES as
    success -- the winner already put the schema/view in the state this
    replica wanted, so boot must proceed rather than crash the pod -- and
    must leave the session usable rather than poisoned.
    """

    async def _sql(_session):
        return "CREATE OR REPLACE VIEW pricing.model_price AS SELECT 1"

    monkeypatch.setattr(pricing_view, "render_pricing_view_sql", _sql)

    session = _FakeSession(second_call_error=_fake_dbapi_error(sqlstate))
    assert await apply_pricing_view(session) is True
    assert session.rolled_back is True, "a caught race must roll back the poisoned transaction"
    assert session.committed is False


async def test_apply_re_raises_a_genuine_ddl_error(monkeypatch):
    """A column-shape change (an edited select, or any other real bug in the
    generated SQL) makes Postgres refuse CREATE OR REPLACE VIEW with a
    non-race SQLSTATE (42P16 invalid_object_definition, confirmed against a
    real Postgres for a renamed view column). That must still fail loudly
    at boot -- a silently-missing view means the Grafana margin dashboard
    reads stale or absent prices with no signal.
    """

    async def _sql(_session):
        return "CREATE OR REPLACE VIEW pricing.model_price AS SELECT 1"

    monkeypatch.setattr(pricing_view, "render_pricing_view_sql", _sql)

    session = _FakeSession(second_call_error=_fake_dbapi_error("42P16"))
    with pytest.raises(DBAPIError):
        await apply_pricing_view(session)
