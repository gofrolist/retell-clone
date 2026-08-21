import pytest

from arhiteq_api.db import session_factory
from arhiteq_api.models import ModelCostRate, PriceRule
from arhiteq_api.services.pricing import model_prices
from arhiteq_api.services.pricing_seed import seed_pricing_defaults

LIVE = "gemini-live-2.5-flash-native-audio"
TEXT = "gemini-3.1-flash-lite"


async def _seeded():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        return {p.model_id: p for p in await model_prices(session)}


async def test_live_model_costs_audio_tokens_not_text_turns():
    # 25 tok/s = 1500/min: input for the whole minute, output for 50% talk.
    # 1500/1e6*3.00 + 0.5*1500/1e6*12.00 = 0.0045 + 0.009
    prices = await _seeded()
    assert prices[LIVE].cost_per_min_model == pytest.approx(0.0135)


async def test_live_stack_excludes_stt_and_tts():
    """Speech-to-speech has no separate synthesis leg to pay for."""
    prices = await _seeded()
    assert prices[LIVE].cost_per_min_stack == pytest.approx(prices[LIVE].cost_per_min_model)


async def test_text_stack_includes_stt_and_tts():
    prices = await _seeded()
    model_only = prices[TEXT].cost_per_min_model
    assert prices[TEXT].cost_per_min_stack == pytest.approx(model_only + 0.0022 + 0.014)


async def test_global_markup_applies_when_no_model_rule_exists():
    prices = await _seeded()
    p = prices[LIVE]
    assert p.rule_source == "global"
    assert p.price_per_min == pytest.approx(p.cost_per_min_stack * 4.0)


async def test_model_rule_beats_global():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(PriceRule(scope=LIVE, markup_pct=100.0, note="test"))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    assert prices[LIVE].rule_source == "model"
    assert prices[LIVE].price_per_min == pytest.approx(prices[LIVE].cost_per_min_stack * 2.0)
    assert prices[TEXT].rule_source == "global"


async def test_explicit_price_beats_markup_and_ignores_cost():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(PriceRule(scope=LIVE, explicit_per_minute_usd=0.09, markup_pct=100.0))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    assert prices[LIVE].rule_source == "explicit"
    assert prices[LIVE].price_per_min == pytest.approx(0.09)


async def test_fixed_adder_applies_after_markup():
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(PriceRule(scope=LIVE, markup_pct=0.0, fixed_per_minute_usd=0.05))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    p = prices[LIVE]
    assert p.price_per_min == pytest.approx(p.cost_per_min_stack + 0.05)


async def test_a_call_is_priced_with_the_rate_in_force_then():
    """A provider reprice must not silently restate last quarter's costs."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(
            ModelCostRate(
                model_id=LIVE,
                input_per_1m_usd=6.00,
                output_per_1m_usd=24.00,
                is_audio=True,
                effective_from_ms=2_000_000,
                note="doubled",
            )
        )
        await session.commit()

        before = {p.model_id: p for p in await model_prices(session, at_ms=1_000_000)}
        after = {p.model_id: p for p in await model_prices(session, at_ms=3_000_000)}

    assert before[LIVE].cost_per_min_model == pytest.approx(0.0135)
    assert after[LIVE].cost_per_min_model == pytest.approx(0.027)


async def test_effective_multiplier_reconciles_breakdown_with_headline():
    """estimates.ts multiplies every component by this; the rows must sum to price."""
    prices = await _seeded()
    p = prices[TEXT]
    assert p.cost_per_min_stack * p.effective_multiplier == pytest.approx(p.price_per_min)
