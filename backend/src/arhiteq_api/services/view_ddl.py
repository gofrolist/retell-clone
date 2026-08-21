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
#   23505 unique_violation  -- what Postgres 18 + asyncpg actually raises here:
#                              a duplicate key on the pg_namespace or pg_type
#                              system-catalog index.
#   42P06 duplicate_schema
#   42P07 duplicate_table   -- CREATE VIEW has no IF NOT EXISTS and, unlike
#                              CREATE OR REPLACE, fails outright if another
#                              replica created the view between this one's DROP
#                              and CREATE. The winner installed the identical
#                              definition, so this is success.
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


async def apply_views(session: AsyncSession, schema: str, views: Sequence[tuple[str, str]]) -> bool:
    """Install `views` (qualified_name, create_sql) into `schema`. Idempotent.

    DROP-then-CREATE, not CREATE OR REPLACE: Postgres refuses to replace a view
    whose column names, types or order changed (42P16 invalid_object_definition),
    so with OR REPLACE any future edit to a select's shape would crash every API
    replica at boot -- taking the whole control plane down for the sake of a view
    only Grafana reads. Dropping first cannot hit that error at all. Both
    statements run in one transaction and Postgres holds the ACCESS EXCLUSIVE
    lock until commit, so a concurrent reader waits rather than seeing the view
    momentarily absent.

    Grants survive this only because the operator sets ALTER DEFAULT PRIVILEGES
    (see infra/sql/grafana_ro.sql); a plain GRANT is dropped along with the view
    and would come back missing on the next boot.
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
