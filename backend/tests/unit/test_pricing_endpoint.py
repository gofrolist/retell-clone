import logging

from sqlalchemy import delete

from arhiteq_api.db import session_factory
from arhiteq_api.models import CostRate, PriceRule
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
        assert set(model) == {
            "model_id",
            "is_audio",
            "input_per_1m",
            "output_per_1m",
            "per_minute",
            "per_minute_adder",
        }


async def test_prices_are_marked_up_above_the_seeded_cost(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    live = next(m for m in body["models"] if m["model_id"] == LIVE)
    assert live["per_minute"] > 0.0135
    assert live["input_per_1m"] > 3.0

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
        assert served_model["input_per_1m"] > cost.input_per_1m_cost, model_id
        assert served_model["output_per_1m"] > cost.output_per_1m_cost, model_id


async def test_returns_the_assumptions_the_view_used(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    assert body["assumptions"]["audio_tokens_per_sec"] == 25.0
    assert body["assumptions"]["agent_talk_ratio"] == 0.5


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
