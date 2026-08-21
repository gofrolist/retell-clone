"""Expose the price rule to Grafana as a Postgres view.

Grafana reads SQL, not Python, so the rule has to exist in the database. It is
not written a second time: the same Select from services/pricing.py is compiled
to Postgres and wrapped in CREATE OR REPLACE VIEW, so the view and the endpoint
cannot disagree.

For Grafana to actually be limited to margin data, its role must be granted
SELECT on this view and nothing on the tables underneath it — that grant does
not exist yet (it is a later task); this view only makes that restriction
possible, it does not enforce it by itself.

`at_ms` is a Postgres now() expression (see `_NOW_MS` below), so a rate change
reaches the view on the very next query with no redeploy. The assumptions
folded into the arithmetic (audio_tokens_per_sec, agent_talk_ratio, ...) are
not: `render_pricing_view_sql` inlines them as literals because a view cannot
carry bind parameters, so an edited assumption only reaches the view the next
time `apply_pricing_view` runs at boot. Read "re-evaluates per query" as
applying to the rate/rule tables only, not to the assumptions.
"""

from sqlalchemy import func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from .pricing import load_assumptions, model_price_select

VIEW_NAME = "pricing.model_price"

# A SQL expression, not a Python int: passing now_ms() would inline a
# millisecond literal at CREATE VIEW time and freeze the view's idea of "now"
# until someone redeploys, so a provider reprice would never reach the
# dashboard on its own. func.now() is evaluated by Postgres on every query
# instead, so the view always shows the rate in force right now.
_NOW_MS = func.floor(func.extract("epoch", func.now()) * 1000)

# SQLSTATEs a losing replica can surface when CREATE SCHEMA IF NOT EXISTS or
# CREATE OR REPLACE VIEW races another replica's identical DDL: four API
# replicas run apply_pricing_view at once on boot, and Postgres's "does it
# already exist" checks are not isolated from concurrent DDL, so two sessions
# can both pass the check and then collide.
#   23505 unique_violation   -- what Postgres 18 + asyncpg actually raises
#                                here: a duplicate key on the pg_namespace or
#                                pg_type system-catalog index (reproduced
#                                below with concurrent apply_pricing_view
#                                calls against a real Postgres).
#   42P06 duplicate_schema, 42710 duplicate_object, 40001 serialization
#   failure ("tuple concurrently updated") -- documented alternate outcomes
#   of the same race; timing and Postgres version decide which of these vs.
#   23505 a given loser actually hits, so all are treated as success.
# Deliberately NOT caught: 42P16 invalid_object_definition, raised when the
# generated SELECT's column names/types/order changed since the view was
# last created (Postgres refuses to CREATE OR REPLACE a view across a shape
# change -- confirmed against a real Postgres). That is a real bug in the
# generated SQL, not a race, and must still fail loudly at boot; the fix is
# `DROP VIEW pricing.model_price` followed by a plain `CREATE VIEW`, not a
# broader except clause here.
_RACE_SQLSTATES = frozenset({"23505", "42P06", "42710", "40001"})


def _is_concurrent_ddl_race(error: DBAPIError) -> bool:
    return getattr(error.orig, "sqlstate", None) in _RACE_SQLSTATES


async def render_pricing_view_sql(session: AsyncSession) -> str:
    assumptions = await load_assumptions(session)
    stmt = model_price_select(assumptions, at_ms=_NOW_MS)
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return f"CREATE OR REPLACE VIEW {VIEW_NAME} AS {compiled}"


async def apply_pricing_view(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return False
    try:
        await session.execute(text("CREATE SCHEMA IF NOT EXISTS pricing"))
        await session.execute(text(await render_pricing_view_sql(session)))
        await session.commit()
    except DBAPIError as error:
        if not _is_concurrent_ddl_race(error):
            raise
        # A Postgres error poisons the transaction until rolled back, so the
        # session must be rolled back before returning -- otherwise the next
        # statement run on it (e.g. the lifespan's next startup step) would
        # fail with "current transaction is aborted" even though the schema
        # and view the winning replica created are exactly what we wanted.
        await session.rollback()
    return True
