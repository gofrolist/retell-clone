from sqlalchemy import func, select

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


async def test_seed_does_not_overwrite_an_operator_edit():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        rule = await session.scalar(select(PriceRule).where(PriceRule.scope == "*"))
        rule.markup_pct = 150
        await session.commit()

        await seed_pricing_defaults(session)
        rule = await session.scalar(select(PriceRule).where(PriceRule.scope == "*"))
        assert float(rule.markup_pct) == 150.0
