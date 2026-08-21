import logging

import pytest
from sqlalchemy import literal, select
from sqlalchemy.exc import DBAPIError

from arhiteq_api.db import session_factory
from arhiteq_api.services import view_ddl
from arhiteq_api.services.view_ddl import apply_views, render_view_sql

# A select, not SQL: apply_views compiles only after it knows it is on
# Postgres, so nothing is rendered on a SQLite boot.
_ONE_VIEW = [("metrics.example", select(literal(1).label("one")))]


def test_render_view_sql_is_a_plain_create_view_with_no_bind_params():
    """A view cannot carry parameters, so nothing may render as a placeholder.

    Plain CREATE VIEW rather than CREATE OR REPLACE: `apply_views` drops the
    view first, which is what keeps a future change to a select's column shape
    from crashlooping every API replica at boot (42P16).
    """
    sql = render_view_sql("metrics.example", select(literal(1).label("one")))

    assert sql.startswith("CREATE VIEW metrics.example AS")
    assert "CREATE OR REPLACE" not in sql
    assert "%(" not in sql and "?" not in sql


async def test_apply_views_is_skipped_on_sqlite():
    """SQLite has no schemas; the suite must not need a Postgres to run."""
    async with session_factory()() as session:
        assert await apply_views(session, "metrics", _ONE_VIEW) is False


class _FakeBind:
    class dialect:
        name = "postgresql"


# The DDL apply_views issues for a single view, in order. The 1-based index of
# each is what `_FakeSession` fails on, so a race test can pick which statement
# loses.
LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL = 1, 2, 3, 4


class _FakeSession:
    """Just enough of AsyncSession's surface for apply_views: one execute() per
    statement (SET LOCAL lock_timeout, CREATE SCHEMA, then DROP + CREATE per
    view), then commit() on success or rollback() on a caught race or lock
    timeout.

    SQLite -- what the rest of this suite runs on -- has no schemas, no
    Postgres SQLSTATEs, and (per test_apply_views_is_skipped_on_sqlite)
    apply_views no-ops on it before reaching any of this logic. A genuine
    concurrent race is also inherently racy to trigger on demand. So the only
    way to exercise the exception-handling branch deterministically in the
    pytest suite is to simulate the DBAPIError a losing replica gets from
    Postgres, the way this fake does. Real concurrency was exercised
    separately, manually, against a throwaway postgres:18 container -- see the
    PR/task report for that run's output; it is not part of this automated
    suite because the suite has no Postgres available.
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
    return DBAPIError("CREATE VIEW metrics.example AS ...", None, orig)


async def test_apply_drops_each_view_before_creating_it():
    """The crashloop this ordering exists to prevent.

    Postgres refuses CREATE OR REPLACE VIEW when the select's column names,
    types or order change (42P16), so the first rollout carrying such a change
    would fail in the lifespan and take every API replica down with it -- for
    views only Grafana reads. Dropping first makes a shape change a normal
    deploy.
    """
    session = _FakeSession()
    assert await apply_views(session, "metrics", _ONE_VIEW) is True

    assert session.committed is True
    assert len(session.calls) == 4
    assert session.calls[LOCK_CALL - 1] == view_ddl._SET_LOCK_TIMEOUT_SQL
    assert session.calls[SCHEMA_CALL - 1] == "CREATE SCHEMA IF NOT EXISTS metrics"
    assert session.calls[DROP_CALL - 1] == "DROP VIEW IF EXISTS metrics.example"
    assert session.calls[CREATE_CALL - 1].startswith("CREATE VIEW metrics.example")


async def test_apply_installs_every_view_in_one_transaction():
    """All of a schema's views land together or none of them do.

    A partial install would leave the dashboard reading a mix of old and new
    definitions, which is worse than reading old ones consistently.
    """
    session = _FakeSession()
    views = [
        ("metrics.first", select(literal(1).label("one"))),
        ("metrics.second", select(literal(2).label("two"))),
    ]

    assert await apply_views(session, "metrics", views) is True

    assert session.committed is True
    assert session.calls[2] == "DROP VIEW IF EXISTS metrics.first"
    assert session.calls[3].startswith("CREATE VIEW metrics.first AS")
    assert session.calls[4] == "DROP VIEW IF EXISTS metrics.second"
    assert session.calls[5].startswith("CREATE VIEW metrics.second AS")


@pytest.mark.parametrize("sqlstate", sorted(view_ddl._RACE_SQLSTATES))
@pytest.mark.parametrize("fail_on", [SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_swallows_a_known_concurrent_ddl_race(sqlstate, fail_on):
    """Any of the boot statements can lose a race with another replica's
    identical DDL and raise one of a known set of SQLSTATEs rather than
    failing cleanly -- Postgres's existence checks are not isolated from
    concurrent DDL (reproduced for real: four replicas booting at once against
    Postgres 18 hit 23505 unique_violation on the pg_namespace/pg_type system
    catalog indexes). Parametrised over the statement that loses, because the
    handling has to cover the DROP and the CREATE, not just whichever one
    happens to be second. apply_views must treat every SQLSTATE in
    _RACE_SQLSTATES as success -- the winner already put the schema/view in
    the state this replica wanted, so boot must proceed rather than crash the
    pod -- and must leave the session usable rather than poisoned.
    """
    session = _FakeSession(_fake_dbapi_error(sqlstate), fail_on=fail_on)
    assert await apply_views(session, "metrics", _ONE_VIEW) is True
    assert session.rolled_back is True, "a caught race must roll back the poisoned transaction"
    assert session.committed is False


async def test_apply_sets_a_short_lock_timeout_before_the_ddl():
    """Without a bound, a Grafana query holding an ACCESS SHARE lock on a view
    blocks DROP VIEW indefinitely -- and with it the FastAPI lifespan, so the
    pod never becomes ready. SET LOCAL scopes the timeout to this transaction
    only, so it cannot leak onto a pooled connection's later, unrelated
    queries.
    """
    session = _FakeSession()
    assert await apply_views(session, "metrics", _ONE_VIEW) is True

    assert session.calls[LOCK_CALL - 1] == "SET LOCAL lock_timeout = '5000ms'"
    # It must run before any of the DDL it is meant to bound.
    assert session.calls[LOCK_CALL - 1] == session.calls[0]


@pytest.mark.parametrize("fail_on", [LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_swallows_a_lock_timeout_and_keeps_the_previous_views(fail_on, caplog):
    """A losing wait for that lock_timeout (55P03 lock_not_available) must not
    fail the lifespan: the views already exist from a previous boot and are
    still serving Grafana, so a blocked refresh is logged and treated as
    success rather than propagated -- unlike a genuine SQL error below, which
    still must fail loudly. Parametrised over every statement in the
    transaction, because any of them can be the one waiting on the lock.
    """
    session = _FakeSession(_fake_dbapi_error(view_ddl._LOCK_TIMEOUT_SQLSTATE), fail_on=fail_on)
    with caplog.at_level(logging.WARNING):
        assert await apply_views(session, "metrics", _ONE_VIEW) is True

    assert session.rolled_back is True, "a caught timeout must roll back the poisoned transaction"
    assert session.committed is False
    assert "lock" in caplog.text.lower()
    # The schema is what tells an operator which dashboard just went stale.
    assert "metrics" in caplog.text


@pytest.mark.parametrize("fail_on", [LOCK_CALL, SCHEMA_CALL, DROP_CALL, CREATE_CALL])
async def test_apply_re_raises_a_genuine_ddl_error(fail_on):
    """A real bug in the generated SQL -- a syntax error (42601 syntax_error)
    is the archetype now that a column-shape change can no longer produce
    42P16 -- must still fail loudly at boot, whichever statement raises it: a
    silently-missing view means the dashboards read stale or absent data with
    no signal.
    """
    session = _FakeSession(_fake_dbapi_error("42601"), fail_on=fail_on)
    with pytest.raises(DBAPIError):
        await apply_views(session, "metrics", _ONE_VIEW)
