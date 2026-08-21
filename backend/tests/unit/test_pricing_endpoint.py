import logging

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from arhiteq_api.db import session_factory
from arhiteq_api.models import CostRate, PriceRule, now_ms
from arhiteq_api.services.pricing import model_prices
from tests.conftest import AUTH_HEADERS

LIVE = "gemini-live-2.5-flash-native-audio"

ALL_MODEL_IDS = {
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-live-preview",
    LIVE,
}
TEXT_MODEL_IDS = ALL_MODEL_IDS - {"gemini-3.1-flash-live-preview", LIVE}


async def test_returns_a_price_for_every_catalog_model(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    assert {m["model_id"] for m in body["models"]} == ALL_MODEL_IDS


async def test_never_serialises_a_cost(client):
    """The business leak this endpoint exists to prevent.

    Failure here is silent and commercial, not a crash: a cost field in a
    customer-facing payload hands away the margin.
    """
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    blob = str(body)

    assert "cost" not in blob.lower()
    # The seeded Live stack cost, to 4dp — must not appear anywhere.
    assert "0.0135" not in blob

    # The literal above only ever proved Live's cost was absent. Every other
    # model's stack cost must be absent too, or a bug that serialises
    # `cost_per_min_stack` instead of `price_per_min` for one of the five
    # TEXT models would pass silently. Query each model's cost independently
    # via `model_prices()` -- the same select the endpoint runs -- rather
    # than hardcoding a second table of numbers next to the one already
    # above.
    async with session_factory()() as session:
        costs = await model_prices(session)
    assert {c.model_id for c in costs} == ALL_MODEL_IDS
    for cost in costs:
        assert cost.cost_per_min_stack is not None, cost.model_id
        needle = f"{cost.cost_per_min_stack:.4f}"
        assert needle not in blob, (cost.model_id, needle)

    for model in body["models"]:
        assert set(model) == {"model_id", "is_audio", "per_minute"}

    # Not a cost VALUE, but equivalent to publishing one. Google's per-token
    # rates are public, so a marked-up `input_per_1m`/`output_per_1m` divided
    # by the published rate is our exact markup, and the `pricing_assumptions`
    # (audio_tokens_per_sec, agent_talk_ratio, turns_per_min, ...) are the rest
    # of the recipe: with them, `per_minute` reduces to our cost stack. The
    # greps above would never catch that — the leaked numbers are prices, and
    # the arithmetic happens on the reader's side. So the keys themselves must
    # be absent, and no frontend code reads them.
    assert "assumptions" not in body
    for model in body["models"]:
        assert "input_per_1m" not in model
        assert "output_per_1m" not in model


async def test_prices_are_marked_up_above_the_seeded_cost(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    live = next(m for m in body["models"] if m["model_id"] == LIVE)
    assert live["per_minute"] > 0.0135

    # The check above only ever exercised the Live model. Every OTHER model
    # must clear the same bar too, or serving a text model's cost instead of
    # its price -- or dropping `* effective_multiplier` for a single entry in
    # the list comprehension -- would emit a plausible float this test never
    # looks at. Derive each model's bound from `model_prices()` (the same
    # query the endpoint itself runs) rather than a hardcoded table of
    # numbers copied out of the implementation, so the assertion still means
    # something if the seeded markup or cost inputs ever change.
    async with session_factory()() as session:
        costs = {p.model_id: p for p in await model_prices(session)}

    served = {m["model_id"]: m for m in body["models"]}
    assert served  # a passing loop over nothing would prove nothing
    for model_id, served_model in served.items():
        cost = costs[model_id]
        assert cost.cost_per_min_stack is not None, model_id
        assert served_model["per_minute"] > cost.cost_per_min_stack, model_id


async def test_requires_authentication(client):
    assert (await client.get("/dashboard/pricing/models")).status_code == 401


async def test_a_model_with_no_usable_rule_is_omitted_and_named_unpriced(client, caplog):
    """Deleting the STT rate blanks price_per_min for every text model (their
    stack cost depends on it) while Live — which has no STT leg — keeps its
    price. The blanked models must vanish from `models`, not render as
    $0.00, and must be named in `unpriced` instead."""
    async with session_factory()() as session:
        await session.execute(delete(CostRate).where(CostRate.component == "cartesia_stt"))
        await session.commit()

    with caplog.at_level(logging.WARNING):
        body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()

    returned_ids = {m["model_id"] for m in body["models"]}
    assert returned_ids == ALL_MODEL_IDS - TEXT_MODEL_IDS
    assert set(body["unpriced"]) == TEXT_MODEL_IDS
    for model_id in TEXT_MODEL_IDS:
        assert model_id in caplog.text


async def test_components_key_is_omitted_when_no_globally_priced_model_exists(client):
    """With every price rule gone, nothing resolves rule_source == 'global',
    so there is no per-model multiplier to price the shared STT/TTS legs
    with. Serialising them unmarked-up would leak raw cost, so the whole key
    must be absent rather than present-but-wrong."""
    async with session_factory()() as session:
        await session.execute(delete(PriceRule))
        await session.commit()

    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()

    assert body["models"] == []
    assert set(body["unpriced"]) == ALL_MODEL_IDS
    assert "components" not in body


@pytest.mark.parametrize(
    ("rule", "label"),
    [
        # An operator zeroing the rule out rather than deleting the row: a
        # price exactly equal to cost, carrying rule_source "model".
        ({"markup_pct": 0.0}, "a zero markup prices at cost"),
        # A flat price set below what the minute costs to serve — nothing in
        # the rule tables can know that, since it never looks at a cost.
        ({"explicit_per_minute_usd": 0.001}, "an explicit price under cost"),
    ],
)
async def test_a_rule_that_prices_at_or_below_cost_is_refused(client, caplog, rule, label):
    """The invariant no rule table can enforce on its own.

    `test_prices_are_marked_up_above_the_seeded_cost` only ever proved the
    SEED data is marked up; with a per-model rule in play the price is
    whatever an operator inserted. A price at or under cost is not a price —
    it must be omitted and named in `unpriced`, exactly like a price no rule
    resolved, so the frontend falls back to its compiled-in card instead of
    quoting a zero-margin minute.
    """
    async with session_factory()() as session:
        session.add(PriceRule(scope=LIVE, note=label, **rule))
        await session.commit()

    with caplog.at_level(logging.WARNING):
        body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()

    assert LIVE not in {m["model_id"] for m in body["models"]}, label
    assert LIVE in body["unpriced"], label
    # The log has to carry the numbers, or an operator sees a model vanish
    # from the picker with nothing to explain why.
    assert "at or below cost" in caplog.text
    assert "0.013500" in caplog.text  # the Live stack cost the rule failed to clear
    # Every other model still resolves through the untouched global rule: the
    # guard drops one bad price, it does not blank the whole card.
    assert {m["model_id"] for m in body["models"]} == ALL_MODEL_IDS - {LIVE}


async def test_the_database_rejects_a_rule_that_can_only_price_below_cost(client):
    """A negative markup or adder can never yield a price above cost, so it is
    refused at the table rather than left for the endpoint to filter. (SQLite
    enforces CHECK constraints, so this covers the real production DDL.)"""
    for kwargs in (
        {"markup_pct": -10.0},
        {"fixed_per_minute_usd": -0.05},
        {"explicit_per_minute_usd": 0.0},
        {"explicit_per_minute_usd": -0.01},
    ):
        async with session_factory()() as session:
            session.add(PriceRule(scope=LIVE, note="bad", **kwargs))
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()


async def test_a_rule_that_leaves_a_lever_unset_is_still_accepted(client):
    """NULL is "this rule does not use this lever", not a violated CHECK — the
    constraints above must not break the shape services/pricing.py relies on."""
    async with session_factory()() as session:
        session.add(PriceRule(scope=LIVE, markup_pct=500.0, note="no adder, no explicit"))
        await session.commit()

    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    live = next(m for m in body["models"] if m["model_id"] == LIVE)
    assert live["per_minute"] == pytest.approx(0.0135 * 6.0)


async def test_a_future_dated_component_rate_is_not_served_early(client):
    """The components block must be effective-dated like everything else.

    A Cartesia rise scheduled for next week is a supported and tested feature
    of the cost tables. Selecting component rates without a date filter serves
    that future rate as today's STT/TTS price while `per_minute` still
    reflects the current one — a breakdown that no longer sums to its own
    headline — and with two rows for the component, whichever row the database
    returns last silently wins.
    """
    async with session_factory()() as session:
        session.add(
            CostRate(
                component="cartesia_tts",
                unit="per_minute",
                unit_price_usd=0.028,  # doubled, effective a week from now
                effective_from_ms=now_ms() + 7 * 24 * 60 * 60 * 1000,
                note="scheduled rise",
            )
        )
        await session.commit()

    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()

    # Today's rate at the seeded 300% global markup: 0.014 * 4.
    assert body["components"]["cartesia_tts"] == pytest.approx(0.056)
    # And the breakdown still reconciles: a TEXT model's headline covers
    # model + STT + TTS at the same markup, so the components can never
    # exceed it.
    text = next(m for m in body["models"] if not m["is_audio"])
    assert (
        body["components"]["cartesia_stt"] + body["components"]["cartesia_tts"] < text["per_minute"]
    )
