"""First-boot pricing data.

Rows are inserted only when a table is empty, so an operator edit is never
overwritten by a redeploy. Prices carry their source in `note`: a price with no
provenance cannot be re-checked and quietly rots.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import CostRate, ModelCostRate, PriceRule, PricingAssumption

_GOOGLE = "ai.google.dev/gemini-api/docs/pricing, paid tier, read 2026-07-31"

# (model_id, input_per_1m, output_per_1m, is_audio)
MODEL_COSTS: tuple[tuple[str, float, float, bool], ...] = (
    ("gemini-3.5-flash", 1.50, 9.00, False),
    ("gemini-3.1-flash-lite", 0.25, 1.50, False),
    ("gemini-2.5-pro", 1.25, 10.00, False),
    ("gemini-2.5-flash", 0.30, 2.50, False),
    ("gemini-2.5-flash-lite", 0.10, 0.40, False),
    ("gemini-3.1-flash-live-preview", 3.00, 12.00, True),
    ("gemini-live-2.5-flash-native-audio", 3.00, 12.00, True),
)

# (component, unit, price, note)
COMPONENT_COSTS: tuple[tuple[str, str, float, str], ...] = (
    ("cartesia_stt", "per_minute", 0.0022, "1 credit/sec at $37.375/M credits, Scale tier"),
    ("cartesia_tts", "per_minute", 0.014, "~1 credit/char, ~750 chars/min speech, 50% talk"),
    ("telnyx_outbound", "per_minute", 0.005, "Elastic SIP, US local outbound, read 2026-08-20"),
    ("telnyx_inbound", "per_minute", 0.0032, "Elastic SIP, US local inbound, read 2026-08-20"),
    ("telnyx_did", "per_month", 2.00, "2 numbers x $1.00/mo"),
    ("kb_overhead", "per_minute", 0.001, "embedding/retrieval, own estimate"),
    (
        "infra_fixed_monthly",
        "per_month",
        1500.00,
        "GKE+CloudSQL+Redis+LB, GCP billing catalog us-east1, read 2026-08-20",
    ),
)

ASSUMPTIONS: tuple[tuple[str, float, str], ...] = (
    ("audio_tokens_per_sec", 25.0, "Google tokenizes audio at 25 tokens/second"),
    ("agent_talk_ratio", 0.5, "share of a call minute the agent speaks — assumed, not measured"),
    ("turns_per_min", 4.0, "assumed LLM requests per call minute"),
    ("output_tokens_per_turn", 150.0, "assumed visible tokens per response"),
    ("display_input_tokens_per_turn", 1500.0, "prompt-independent budget for the picker badge"),
)

# A placeholder, not a recommendation: 300% puts a Live minute near $0.054
# against ~$0.018 cost, under Retell's published $0.07 floor. An operator sets
# the real number before the customer-visible switch.
DEFAULT_MARKUP_PCT = 300.0


async def _is_empty(session: AsyncSession, model: type) -> bool:
    return (await session.scalar(select(func.count()).select_from(model))) == 0


async def seed_pricing_defaults(session: AsyncSession) -> None:
    """Insert first-boot pricing rows. Idempotent and safe on concurrent boots."""
    if await _is_empty(session, ModelCostRate):
        session.add_all(
            ModelCostRate(
                model_id=model_id,
                input_per_1m_usd=inp,
                output_per_1m_usd=out,
                is_audio=is_audio,
                effective_from_ms=0,
                note=_GOOGLE,
            )
            for model_id, inp, out, is_audio in MODEL_COSTS
        )
    if await _is_empty(session, CostRate):
        session.add_all(
            CostRate(component=component, unit=unit, unit_price_usd=price, note=note)
            for component, unit, price, note in COMPONENT_COSTS
        )
    if await _is_empty(session, PricingAssumption):
        session.add_all(
            PricingAssumption(key=key, value=value, note=note) for key, value, note in ASSUMPTIONS
        )
    if await _is_empty(session, PriceRule):
        session.add(
            PriceRule(
                scope="*",
                markup_pct=DEFAULT_MARKUP_PCT,
                note="PLACEHOLDER — operator sets the real markup before the UI switch",
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        # Another replica seeded between the emptiness check and the commit.
        # The unique constraints are what make that a no-op rather than a
        # duplicate rate card, which would price calls at random.
        await session.rollback()
