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


async def test_apply_pricing_view_installs_the_view_through_the_shared_installer(monkeypatch):
    """The DROP-then-CREATE contract, the lock timeout and the concurrent-boot
    races live in services/view_ddl.py and are tested there. What this module
    still owns is *which* view goes into *which* schema -- a wrong schema here
    would leave Grafana's grant pointing at nothing.
    """
    seen: dict[str, object] = {}

    async def _fake_apply_views(session, schema, views):
        seen["schema"] = schema
        seen["views"] = views
        return True

    monkeypatch.setattr(pricing_view, "apply_views", _fake_apply_views)

    assert await apply_pricing_view(object()) is True
    assert seen["schema"] == "pricing"
    [(name, sql)] = seen["views"]
    assert name == "pricing.model_price"
    assert sql.startswith("CREATE VIEW pricing.model_price AS")
