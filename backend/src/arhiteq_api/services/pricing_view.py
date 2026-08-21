"""Expose the price rule to Grafana as a Postgres view.

Grafana reads SQL, not Python, so the rule has to exist in the database. It is
not written a second time: the same Select from services/pricing.py is compiled
to Postgres and wrapped in CREATE OR REPLACE VIEW, so the view and the endpoint
cannot disagree.

Grafana's role gets SELECT on this view and on nothing underneath it, so the
cost tables stay unreachable while the dashboard still computes margin.
"""

from sqlalchemy import func, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from .pricing import load_assumptions, model_price_select

VIEW_NAME = "pricing.model_price"

# A SQL expression, not a Python int: passing now_ms() would inline a
# millisecond literal at CREATE VIEW time and freeze the view's idea of "now"
# until someone redeploys, so a provider reprice would never reach the
# dashboard on its own. func.now() is evaluated by Postgres on every query
# instead, so the view always shows the rate in force right now.
_NOW_MS = func.floor(func.extract("epoch", func.now()) * 1000)


async def render_pricing_view_sql(session: AsyncSession) -> str:
    assumptions = await load_assumptions(session)
    stmt = model_price_select(assumptions, at_ms=_NOW_MS)
    compiled = stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return f"CREATE OR REPLACE VIEW {VIEW_NAME} AS {compiled}"


async def apply_pricing_view(session: AsyncSession) -> bool:
    if session.get_bind().dialect.name != "postgresql":
        return False
    await session.execute(text("CREATE SCHEMA IF NOT EXISTS pricing"))
    await session.execute(text(await render_pricing_view_sql(session)))
    await session.commit()
    return True
