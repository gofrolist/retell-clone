"""The one implementation of cost -> price.

Both the pricing endpoint and the Grafana margin panels read this. Written
twice — once in Python for the API, once in SQL for the dashboard — the two
would drift, and the day they disagree the price list and the margin report
describe the same call differently. So it is one SQLAlchemy Select: the API
executes it, and boot compiles it into the `pricing.model_price` view.

The select is deliberately dialect-free. Tests run on SQLite and production is
Postgres; anything that renders on only one of them (LATERAL, DISTINCT ON,
FILTER) would make the view and the API two implementations again.
"""

from typing import Any, NamedTuple

from sqlalchemy import Float, Select, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import CostRate, ModelCostRate, PriceRule, PricingAssumption, now_ms


class ModelPrice(NamedTuple):
    model_id: str
    is_audio: bool
    cost_per_min_model: float
    cost_per_min_stack: float
    price_per_min: float
    rule_source: str
    effective_multiplier: float
    input_per_1m_cost: float
    output_per_1m_cost: float


async def load_assumptions(session: AsyncSession) -> dict[str, float]:
    rows = (await session.execute(select(PricingAssumption.key, PricingAssumption.value))).all()
    return {key: float(value) for key, value in rows}


def _current(model: Any, key_column: Any, at_ms: int) -> Any:
    """The row in force at `at_ms` for each key.

    row_number() over a descending effective_from_ms, rather than a max()
    subquery join: the latter duplicates a key that has two rows with the same
    timestamp, which is exactly what a mistaken double-insert produces.
    """
    ranked = (
        select(
            model,
            func.row_number()
            .over(partition_by=key_column, order_by=model.effective_from_ms.desc())
            .label("rn"),
        )
        .where(model.effective_from_ms <= at_ms)
        .subquery()
    )
    return select(ranked).where(ranked.c.rn == 1).subquery()


def model_price_select(assumptions: dict[str, float], at_ms: int) -> Select[Any]:
    tokens_per_min = assumptions["audio_tokens_per_sec"] * 60.0
    talk = assumptions["agent_talk_ratio"]
    turns = assumptions["turns_per_min"]
    out_tokens = assumptions["output_tokens_per_turn"]
    in_tokens = assumptions["display_input_tokens_per_turn"]

    rates = _current(ModelCostRate, ModelCostRate.model_id, at_ms)
    rules = _current(PriceRule, PriceRule.scope, at_ms)
    components = _current(CostRate, CostRate.component, at_ms)

    def component(name: str) -> ColumnElement[Any]:
        return (
            select(components.c.unit_price_usd)
            .where(components.c.component == name)
            .scalar_subquery()
        )

    stt = func.coalesce(component("cartesia_stt"), 0.0)
    tts = func.coalesce(component("cartesia_tts"), 0.0)

    # Audio models bill the audio stream; text models bill turns. Two formulas,
    # not two rates — applying the text formula to a Live model under-prices an
    # audio minute by roughly 6x.
    audio_cost = cast(
        literal(tokens_per_min / 1e6) * rates.c.input_per_1m_usd
        + literal(talk * tokens_per_min / 1e6) * rates.c.output_per_1m_usd,
        Float,
    )
    text_cost = cast(
        literal(turns)
        * (
            literal(in_tokens / 1e6) * rates.c.input_per_1m_usd
            + literal(out_tokens / 1e6) * rates.c.output_per_1m_usd
        ),
        Float,
    )
    cost_model = case((rates.c.is_audio, audio_cost), else_=text_cost)
    # Live replaces STT+LLM+TTS with one model, so it has no synthesis leg.
    cost_stack = case((rates.c.is_audio, cost_model), else_=cost_model + stt + tts)

    # A correlated scalar per column instead of one LATERAL join: LATERAL is
    # Postgres-only, and the tests — like any future SQLite consumer — must see
    # the same arithmetic the view will compute.
    def model_rule(column: Any) -> ColumnElement[Any]:
        return (
            select(column)
            .where(rules.c.scope == rates.c.model_id)
            .correlate(rates)
            .scalar_subquery()
        )

    def global_rule(column: Any) -> ColumnElement[Any]:
        return select(column).where(rules.c.scope == "*").scalar_subquery()

    model_explicit = model_rule(rules.c.explicit_per_minute_usd)
    # `id` is NOT NULL, so it is the only column that distinguishes "no rule for
    # this model" from "a rule that leaves every knob unset".
    model_exists = model_rule(rules.c.id)

    def marked_up(markup: ColumnElement[Any], fixed: ColumnElement[Any]) -> ColumnElement[Any]:
        return cost_stack * (1.0 + func.coalesce(markup, 0.0) / 100.0) + func.coalesce(fixed, 0.0)

    price = case(
        (model_explicit.isnot(None), model_explicit),
        (
            model_exists.isnot(None),
            marked_up(model_rule(rules.c.markup_pct), model_rule(rules.c.fixed_per_minute_usd)),
        ),
        else_=marked_up(global_rule(rules.c.markup_pct), global_rule(rules.c.fixed_per_minute_usd)),
    )
    rule_source = case(
        (model_explicit.isnot(None), literal("explicit")),
        (model_exists.isnot(None), literal("model")),
        else_=literal("global"),
    )

    return select(
        rates.c.model_id.label("model_id"),
        rates.c.is_audio.label("is_audio"),
        cost_model.label("cost_per_min_model"),
        cost_stack.label("cost_per_min_stack"),
        price.label("price_per_min"),
        rule_source.label("rule_source"),
        rates.c.input_per_1m_usd.label("input_per_1m_cost"),
        rates.c.output_per_1m_usd.label("output_per_1m_cost"),
    ).select_from(rates)


async def model_prices(session: AsyncSession, at_ms: int | None = None) -> list[ModelPrice]:
    at = now_ms() if at_ms is None else at_ms
    assumptions = await load_assumptions(session)
    rows = (await session.execute(model_price_select(assumptions, at))).all()
    out: list[ModelPrice] = []
    for row in rows:
        stack = float(row.cost_per_min_stack)
        price = float(row.price_per_min)
        out.append(
            ModelPrice(
                model_id=row.model_id,
                is_audio=bool(row.is_audio),
                cost_per_min_model=float(row.cost_per_min_model),
                cost_per_min_stack=stack,
                price_per_min=price,
                rule_source=row.rule_source,
                # The ratio the frontend multiplies every breakdown row by, so
                # the rows always sum to the headline price — true even for an
                # explicit price or a fixed adder, neither of which can be
                # expressed as a per-token markup.
                effective_multiplier=(price / stack) if stack else 1.0,
                input_per_1m_cost=float(row.input_per_1m_cost),
                output_per_1m_cost=float(row.output_per_1m_cost),
            )
        )
    return out
