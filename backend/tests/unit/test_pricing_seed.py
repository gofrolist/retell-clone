from sqlalchemy import delete, func, select

from arhiteq_api.db import session_factory
from arhiteq_api.models import CostRate, ModelCostRate, PriceRule, PricingAssumption
from arhiteq_api.services.pricing_seed import seed_pricing_defaults


async def test_seed_populates_every_catalog_model():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        models = (await session.scalars(select(ModelCostRate.model_id))).all()

    assert set(models) == {
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-live-preview",
        "gemini-live-2.5-flash-native-audio",
    }


async def test_seed_marks_only_live_models_as_audio():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        audio = (
            await session.scalars(select(ModelCostRate.model_id).where(ModelCostRate.is_audio))
        ).all()

    assert set(audio) == {
        "gemini-3.1-flash-live-preview",
        "gemini-live-2.5-flash-native-audio",
    }


async def test_seed_is_idempotent():
    """Four API replicas boot at once; the second must not double the rows."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await seed_pricing_defaults(session)
        counts = [
            await session.scalar(select(func.count()).select_from(t))
            for t in (ModelCostRate, CostRate, PriceRule, PricingAssumption)
        ]

    assert counts == [7, 7, 1, 5]


async def test_seed_survives_a_genuine_cross_session_collision(monkeypatch):
    """Two replicas boot at once, each on its own session/connection -- unlike
    the other tests above, which reuse one session and so the second call
    only ever sees an already-populated table (the fast, no-insert path;
    see test_seed_is_idempotent).

    Session A wins the race outright: it observes empty tables, seeds, and
    commits. Session B is made to behave as if it had captured its own
    "tables are empty" observation a moment earlier, before A's insert
    landed -- exactly what a second replica racing A at boot would have
    done -- so it goes on to attempt the very same inserts, and its own
    session.commit() is the one that must collide with the rows A already
    committed, hitting the unique constraints that pricing_seed.py's
    `except IntegrityError` exists to catch.

    What this does NOT prove: literal wall-clock parallelism. An earlier
    version of this test tried to get there for real, by having session B
    read (pinning a WAL snapshot) before session A committed, then hoping
    B's later reads inside seed_pricing_defaults still observed that stale
    "empty" snapshot. That turned out to be nondeterministic in this
    harness: whether B's later reads see the pre- or post-commit state
    depends on incidental connection/WAL checkpoint history (e.g. it worked
    against a brand-new database file, but stopped working once the table
    had prior commits on it, as it does here via the autouse fixture's
    first-boot seed). So instead, B's `_is_empty` check is patched to always
    report "empty" for its one call -- standing in for that earlier read
    deterministically rather than hoping SQLite's locking cooperates. What's
    exercised for real, across two independent sessions/connections, is the
    part that actually matters: the IntegrityError raised out of
    session.commit() when two replicas' inserts collide, and
    pricing_seed.py's rollback in response.
    """
    # The autouse _fresh_db fixture already seeded the catalog (it mirrors
    # prod's lifespan hook), so start from a clean slate the way a genuine
    # first boot would.
    async with session_factory()() as setup:
        for table in (ModelCostRate, CostRate, PriceRule, PricingAssumption):
            await setup.execute(delete(table))
        await setup.commit()

    async def _always_empty(_session, _model):
        return True

    async with session_factory()() as session_a, session_factory()() as session_b:
        # Session A wins the race outright: empty tables, seeds, commits.
        await seed_pricing_defaults(session_a)

        # Session B "remembers" observing empty tables (see docstring) and
        # seeds too. Its inserts collide with the rows A already committed,
        # so B's own commit() raises IntegrityError internally --
        # pricing_seed.py must catch it and roll back rather than propagate
        # or leave a partial set.
        monkeypatch.setattr("arhiteq_api.services.pricing_seed._is_empty", _always_empty)
        await seed_pricing_defaults(session_b)

        counts = [
            await session_a.scalar(select(func.count()).select_from(t))
            for t in (ModelCostRate, CostRate, PriceRule, PricingAssumption)
        ]
        assert counts == [7, 7, 1, 5]

        # The loser must still be a usable session after its rollback.
        post_rollback = await session_b.scalar(select(func.count()).select_from(ModelCostRate))
        assert post_rollback == 7


async def test_seed_does_not_overwrite_an_operator_edit():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        rule = await session.scalar(select(PriceRule).where(PriceRule.scope == "*"))
        rule.markup_pct = 150
        await session.commit()

        await seed_pricing_defaults(session)
        rule = await session.scalar(select(PriceRule).where(PriceRule.scope == "*"))
        assert float(rule.markup_pct) == 150.0
