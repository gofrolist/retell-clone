"""Expose the price rule to Grafana as a Postgres view.

Grafana reads SQL, not Python, so the rule has to exist in the database. It is
not written a second time: the same Select from services/pricing.py is compiled
to Postgres and installed as the `pricing.model_price` view, so the view and
the endpoint cannot disagree.

For Grafana to actually be limited to margin data, its role must be granted
SELECT on this view and nothing on the tables underneath it — that grant does
not exist yet (it is a later task); this view only makes that restriction
possible, it does not enforce it by itself.

Nothing is frozen into the view at boot. `at_ms` is a Postgres now()
expression (see `_NOW_MS` below) and every `pricing_assumptions` value is a
scalar subquery (see `assumption()` in services/pricing.py), so a rate change,
a rule change and an assumption edit all reach the view on its very next query
with no redeploy — the same instant they reach the API.

This DDL runs in the FastAPI lifespan, so it blocks startup until it
commits. A `lock_timeout` bounds that wait: without one, a Grafana query
mid-SELECT against the view holds an ACCESS SHARE lock that DROP VIEW must
wait behind, and a stuck DROP VIEW is a stuck lifespan is a pod that never
becomes ready. A timeout there is not a genuine SQL error — the view already
exists from a previous boot and is still serving Grafana just fine — so it
is logged and swallowed rather than propagated, the same as the concurrent-
DDL races below.
"""

import logging

from sqlalchemy import func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from .pricing import model_price_select

logger = logging.getLogger(__name__)

VIEW_NAME = "pricing.model_price"

# A few seconds, not Postgres's default (unlimited): long enough to not fire
# on ordinary contention, short enough that a stuck Grafana query cannot hang
# the FastAPI lifespan past a health-check grace period. Local to this
# transaction only (SET LOCAL), so it never leaks onto a pooled connection's
# later, unrelated queries.
_LOCK_TIMEOUT_MS = 5_000
_SET_LOCK_TIMEOUT_SQL = f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT_MS}ms'"

# A SQL expression, not a Python int: passing now_ms() would inline a
# millisecond literal at CREATE VIEW time and freeze the view's idea of "now"
# until someone redeploys, so a provider reprice would never reach the
# dashboard on its own. func.now() is evaluated by Postgres on every query
# instead, so the view always shows the rate in force right now.
_NOW_MS = func.floor(func.extract("epoch", func.now()) * 1000)

# DROP-then-CREATE, not CREATE OR REPLACE. Postgres refuses to replace a view
# whose column names, types or order changed (42P16 invalid_object_definition),
# so with CREATE OR REPLACE any future edit to the select's shape would crash
# every API replica at boot on rollout -- taking the whole control plane down
# for the sake of a view only Grafana reads. Dropping first cannot hit that
# error at all. Both statements run inside one transaction, and Postgres holds
# the view's ACCESS EXCLUSIVE lock until commit, so a concurrent Grafana query
# waits rather than seeing the view momentarily absent.
_DROP_SQL = f"DROP VIEW IF EXISTS {VIEW_NAME}"

# SQLSTATEs a losing replica can surface when this boot DDL races another
# replica's identical DDL: four API replicas run apply_pricing_view at once on
# boot, and Postgres's "does it already exist" checks are not isolated from
# concurrent DDL, so two sessions can both pass the check and then collide.
#   23505 unique_violation   -- what Postgres 18 + asyncpg actually raises
#                                here: a duplicate key on the pg_namespace or
#                                pg_type system-catalog index (reproduced
#                                with concurrent apply_pricing_view calls
#                                against a real Postgres).
#   42P07 duplicate_table    -- CREATE VIEW has no IF NOT EXISTS and, unlike
#                                CREATE OR REPLACE, fails outright if another
#                                replica created the view between this one's
#                                DROP and CREATE. The winner installed the
#                                identical definition, so this is success.
#   42P06 duplicate_schema, 42710 duplicate_object, 40001 serialization
#   failure ("tuple concurrently updated") -- documented alternate outcomes
#   of the same race; timing and Postgres version decide which of these vs.
#   23505 a given loser actually hits, so all are treated as success.
# Everything else still fails loudly: a syntax error in the generated SQL
# (42601) is a real bug, and a boot that quietly leaves no view means the
# Grafana margin dashboard reads stale or absent prices with no signal.
_RACE_SQLSTATES = frozenset({"23505", "42P06", "42P07", "42710", "40001"})

# lock_not_available: what `_SET_LOCK_TIMEOUT_SQL` above turns a blocked wait
# into. Unlike the races above, this is not "another replica already did the
# job" -- it is "a reader (Grafana) is holding the lock this DDL needs" -- but
# the outcome for boot is the same: the view from a previous boot is already
# there and correct, so a failed refresh must not fail the lifespan.
_LOCK_TIMEOUT_SQLSTATE = "55P03"


def _is_concurrent_ddl_race(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) in _RACE_SQLSTATES


def _is_lock_timeout(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) == _LOCK_TIMEOUT_SQLSTATE


def render_pricing_view_sql() -> str:
    stmt = model_price_select(at_ms=_NOW_MS)
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return f"CREATE VIEW {VIEW_NAME} AS {compiled}"


async def apply_pricing_view(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return False
    try:
        await session.execute(text(_SET_LOCK_TIMEOUT_SQL))
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS pricing"))
        await session.execute(text(_DROP_SQL))
        await session.execute(text(render_pricing_view_sql()))
        await session.commit()
    except DBAPIError as error:
        if _is_lock_timeout(error):
            # Non-fatal by design: the view already exists from a previous
            # boot, so a blocked refresh must not stop this pod from serving
            # traffic. Logged, unlike the races below, because it is a real
            # signal an operator may want to act on (a long-running Grafana
            # query holding the view's lock) rather than routine boot noise.
            logger.warning(
                "pricing view refresh timed out waiting for a lock on %s after %dms; "
                "leaving the existing view from a previous boot in place",
                VIEW_NAME,
                _LOCK_TIMEOUT_MS,
            )
            await session.rollback()
            return True
        if not _is_concurrent_ddl_race(error):
            raise
        # A Postgres error poisons the transaction until rolled back, so the
        # session must be rolled back before returning -- otherwise the next
        # statement run on it (e.g. the lifespan's next startup step) would
        # fail with "current transaction is aborted" even though the schema
        # and view the winning replica created are exactly what we wanted.
        await session.rollback()
    return True
