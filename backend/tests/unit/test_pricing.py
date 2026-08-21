import pytest
from sqlalchemy import delete, literal, select

from arhiteq_api.db import session_factory
from arhiteq_api.models import CostRate, ModelCostRate, PriceRule, now_ms
from arhiteq_api.services.pricing import model_prices, price_columns, scalar_sources
from arhiteq_api.services.pricing_seed import seed_pricing_defaults

LIVE = "gemini-live-2.5-flash-native-audio"
TEXT = "gemini-3.1-flash-lite"

# Computed from the seed by hand, so a test that disagrees with the code is a
# real disagreement and not the code restating itself.
# LIVE (audio): 1500 tok/min in + 50% talk out -> 1500/1e6*3.00 + 750/1e6*12.00
LIVE_COST = 0.0135
# TEXT: 4 turns * (1500/1e6*0.25 + 150/1e6*1.50)
TEXT_MODEL_COST = 0.0024
STT = 0.0022
TTS = 0.014
TEXT_STACK_COST = TEXT_MODEL_COST + STT + TTS  # 0.0186


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
    """The adder is margin, not cost: it must not be marked up as well.

    With a non-zero markup the two orderings diverge — cost*(1+m)+f is 0.077,
    (cost+f)*(1+m) is 0.127 — which is the only way this test can fail.
    """
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(PriceRule(scope=LIVE, markup_pct=100.0, fixed_per_minute_usd=0.05))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    p = prices[LIVE]
    assert p.cost_per_min_stack == pytest.approx(LIVE_COST)
    assert p.price_per_min == pytest.approx(LIVE_COST * 2.0 + 0.05)  # 0.077
    assert p.price_per_min != pytest.approx((LIVE_COST + 0.05) * 2.0)  # 0.127


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


async def test_a_price_rule_only_applies_from_its_effective_date():
    """A markup change is a commercial decision with a date, not a retro-edit."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(
            PriceRule(scope=LIVE, markup_pct=100.0, effective_from_ms=2_000_000, note="later")
        )
        await session.commit()

        before = {p.model_id: p for p in await model_prices(session, at_ms=1_000_000)}
        after = {p.model_id: p for p in await model_prices(session, at_ms=3_000_000)}

    assert before[LIVE].rule_source == "global"
    assert before[LIVE].price_per_min == pytest.approx(LIVE_COST * 4.0)
    assert after[LIVE].rule_source == "model"
    assert after[LIVE].price_per_min == pytest.approx(LIVE_COST * 2.0)


async def test_a_component_rate_only_applies_from_its_effective_date():
    """A vendor's price rise must not restate the cost of minutes already sold."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(
            CostRate(
                component="cartesia_tts",
                unit="per_minute",
                unit_price_usd=0.028,
                effective_from_ms=2_000_000,
                note="doubled",
            )
        )
        await session.commit()

        before = {p.model_id: p for p in await model_prices(session, at_ms=1_000_000)}
        after = {p.model_id: p for p in await model_prices(session, at_ms=3_000_000)}

    assert before[TEXT].cost_per_min_stack == pytest.approx(TEXT_STACK_COST)
    assert after[TEXT].cost_per_min_stack == pytest.approx(TEXT_MODEL_COST + STT + 0.028)


async def test_no_price_rule_reads_as_unknown_not_as_cost():
    """A deleted rule must blank the price, not quietly sell the minute at cost."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await session.execute(delete(PriceRule))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    p = prices[LIVE]
    assert p.cost_per_min_stack == pytest.approx(LIVE_COST)
    assert p.price_per_min is None
    assert p.rule_source == "none"
    assert p.effective_multiplier is None
    assert prices[TEXT].price_per_min is None


async def test_a_rule_with_every_knob_unset_is_not_a_price():
    """An empty row is an operator who has not decided yet, not a 0% markup."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await session.execute(delete(PriceRule))
        session.add(PriceRule(scope="*", note="knobs left unset"))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    assert prices[LIVE].price_per_min is None
    assert prices[LIVE].rule_source == "none"


async def test_global_explicit_price_applies_to_every_model():
    """A flat platform price at scope '*' is a price, not an ignored column."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await session.execute(delete(PriceRule))
        session.add(PriceRule(scope="*", explicit_per_minute_usd=0.12, markup_pct=10.0))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    for model_id in (LIVE, TEXT):
        assert prices[model_id].price_per_min == pytest.approx(0.12)
        assert prices[model_id].rule_source == "explicit"


async def test_missing_component_rate_makes_the_stack_cost_unknown_not_free():
    """A renamed or expired component would otherwise shave 87% off a text minute."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        await session.execute(delete(CostRate).where(CostRate.component == "cartesia_stt"))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    text = prices[TEXT]
    assert text.cost_per_min_model == pytest.approx(TEXT_MODEL_COST)
    assert text.cost_per_min_stack is None
    assert text.price_per_min is None
    assert text.effective_multiplier is None
    # The rule still resolved; it is the cost that is unknown.
    assert text.rule_source == "global"

    # Live has no STT leg, so its price is unaffected.
    assert prices[LIVE].cost_per_min_stack == pytest.approx(LIVE_COST)
    assert prices[LIVE].price_per_min == pytest.approx(LIVE_COST * 4.0)


async def test_effective_multiplier_reconciles_breakdown_with_headline():
    """estimates.ts multiplies every component by this; the rows must sum to price.

    Both cases here are ones a per-token markup cannot express, which is the
    reason the multiplier exists: an explicit price that ignores cost, and a
    fixed adder on top of a markup. Expected numbers are computed from the
    seed by hand rather than from the multiplier's own definition.
    """
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(PriceRule(scope=LIVE, explicit_per_minute_usd=0.09))
        session.add(PriceRule(scope=TEXT, markup_pct=100.0, fixed_per_minute_usd=0.05))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    live = prices[LIVE]
    assert live.price_per_min == pytest.approx(0.09)
    assert live.effective_multiplier == pytest.approx(0.09 / LIVE_COST)  # 6.666...
    assert live.cost_per_min_model * live.effective_multiplier == pytest.approx(0.09)

    text = prices[TEXT]
    expected_price = TEXT_STACK_COST * 2.0 + 0.05  # 0.0872
    assert text.price_per_min == pytest.approx(expected_price)
    assert text.effective_multiplier == pytest.approx(expected_price / TEXT_STACK_COST)
    breakdown = (TEXT_MODEL_COST, STT, TTS)
    assert sum(row * text.effective_multiplier for row in breakdown) == pytest.approx(0.0872)


async def test_effective_multiplier_is_none_when_there_is_nothing_to_scale():
    """A free model with a real price has no ratio; 1.0 would understate it 100%."""
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        session.add(
            ModelCostRate(
                model_id="promo-free-model",
                input_per_1m_usd=0.0,
                output_per_1m_usd=0.0,
                is_audio=True,
                note="zero-cost promo",
            )
        )
        session.add(PriceRule(scope="promo-free-model", explicit_per_minute_usd=0.09))
        await session.commit()
        prices = {p.model_id: p for p in await model_prices(session)}

    p = prices["promo-free-model"]
    assert p.cost_per_min_stack == pytest.approx(0.0)
    assert p.price_per_min == pytest.approx(0.09)
    assert p.effective_multiplier is None


async def test_scalar_sources_price_a_model_the_same_way_the_view_does():
    """The two lookup strategies must be one implementation, not two.

    `model_price_select` reads the rate card as joined relations;
    `scalar_sources` reads it as correlated scalar subqueries so a call can be
    priced at its own start. If these ever disagree, the price endpoint and
    the margin dashboard describe the same minute differently.
    """
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
        at_ms = now_ms()
        from_view = {p.model_id: p for p in await model_prices(session, at_ms=at_ms)}
        assert from_view, "the seed must produce something to compare against"

        for model_id, expected in from_view.items():
            cols = price_columns(scalar_sources(literal(model_id), literal(at_ms)))
            row = (
                (
                    await session.execute(
                        select(
                            cols.cost_per_min_model.label("cost_model"),
                            cols.cost_per_min_stack.label("cost_stack"),
                            cols.price_per_min.label("price"),
                            cols.rule_source.label("rule_source"),
                        )
                    )
                )
                .mappings()
                .one()
            )

            assert row["cost_model"] == pytest.approx(expected.cost_per_min_model), model_id
            assert row["cost_stack"] == pytest.approx(expected.cost_per_min_stack), model_id
            assert row["price"] == pytest.approx(expected.price_per_min), model_id
            assert row["rule_source"] == expected.rule_source, model_id


async def test_scalar_sources_use_the_rate_in_force_at_that_instant():
    """A call is priced at its own start, not at the newest rate card."""
    async with session_factory()() as session:
        session.add_all(
            [
                ModelCostRate(
                    model_id="gemini-test-repriced",
                    input_per_1m_usd=1.0,
                    output_per_1m_usd=1.0,
                    is_audio=True,
                    effective_from_ms=0,
                ),
                ModelCostRate(
                    model_id="gemini-test-repriced",
                    input_per_1m_usd=10.0,
                    output_per_1m_usd=10.0,
                    is_audio=True,
                    effective_from_ms=5_000,
                ),
            ]
        )
        await session.commit()

        def cost_at(at_ms: int):
            cols = price_columns(scalar_sources(literal("gemini-test-repriced"), literal(at_ms)))
            return select(cols.cost_per_min_model.label("cost"))

        before = (await session.execute(cost_at(1_000))).scalar_one()
        after = (await session.execute(cost_at(9_000))).scalar_one()

        assert before > 0
        assert after == pytest.approx(before * 10)


async def test_scalar_sources_yield_null_for_a_model_with_no_rate():
    """Unknown must stay unknown: a 0 here would claim the model is free."""
    async with session_factory()() as session:
        cols = price_columns(
            scalar_sources(literal("model-that-does-not-exist"), literal(now_ms()))
        )
        row = (
            (
                await session.execute(
                    select(
                        cols.cost_per_min_stack.label("cost"),
                        cols.price_per_min.label("price"),
                    )
                )
            )
            .mappings()
            .one()
        )

        assert row["cost"] is None
        assert row["price"] is None
