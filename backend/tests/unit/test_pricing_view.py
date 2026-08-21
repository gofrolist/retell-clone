from arhiteq_api.db import session_factory
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
