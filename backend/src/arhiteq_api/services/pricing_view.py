"""Expose the price rule to Grafana as a Postgres view.

Grafana reads SQL, not Python, so the rule has to exist in the database. It is
not written a second time: the same Select from services/pricing.py is compiled
to Postgres and installed as the `pricing.model_price` view, so the view and
the endpoint cannot disagree.

For Grafana to actually be limited to margin data, its role must be granted
SELECT on this view and nothing on the tables underneath it. That grant is
`infra/sql/grafana_ro.sql`, and it has to arrive as ALTER DEFAULT PRIVILEGES:
boot drops and recreates this view, which takes a plain GRANT down with it.

Nothing is frozen into the view at boot. `at_ms` is a Postgres now()
expression (see `_NOW_MS` below) and every `pricing_assumptions` value is a
scalar subquery (see `assumption()` in services/pricing.py), so a rate change,
a rule change and an assumption edit all reach the view on its very next query
with no redeploy — the same instant they reach the API.

This DDL runs in the FastAPI lifespan, so it blocks startup until it commits.
How that is made safe — the lock timeout that keeps a mid-SELECT Grafana query
from hanging a pod's startup, and the concurrent-boot races a losing replica
must read as success — lives in services/view_ddl.py, which the `metrics`
schema installs through as well.
"""

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
    """Install `pricing.model_price`. Idempotent; a no-op off Postgres.

    The DROP-then-CREATE contract, the concurrent-boot races and the lock
    timeout all live in services/view_ddl.py, which the metrics schema shares.
    """
    return await apply_views(session, "pricing", [(VIEW_NAME, model_price_select(at_ms=_NOW_MS))])
