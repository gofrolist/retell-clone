"""The one implementation of cost -> price.

Both the pricing endpoint and the Grafana margin panels read this. Written
twice — once in Python for the API, once in SQL for the dashboard — the two
would drift, and the day they disagree the price list and the margin report
describe the same call differently. So it is one SQLAlchemy Select: the API
executes it, and boot compiles it into the `pricing.model_price` view.

The select is deliberately dialect-free. Tests run on SQLite and production is
Postgres; anything that renders on only one of them (LATERAL, DISTINCT ON,
FILTER) would make the view and the API two implementations again.

Missing data reads as NULL, never as a number. A cost with no rate and a price
with no rule are *unknown*; coalescing either to 0 turns "we don't know" into
"it's free", which looks like a complete number all the way to the invoice.
So a missing component rate makes `cost_per_min_stack` NULL, and a scope with
no usable rule makes `price_per_min` NULL with `rule_source = 'none'` — the
product never quotes at cost by accident.
"""

from collections.abc import Callable
from typing import Any, NamedTuple

from sqlalchemy import Float, Select, case, cast, func, literal, null, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from ..models import CostRate, ModelCostRate, PriceRule, PricingAssumption, now_ms


class ModelPrice(NamedTuple):
    """One model's cost and price per minute.

    `cost_per_min_model`, `cost_per_min_stack`, `price_per_min` and
    `effective_multiplier` are None when the answer is unknown: a
    `pricing_assumptions` row or a component rate is missing, or no price
    rule resolves, or the stack costs nothing so no ratio can express the
    price. `cost_per_min_model` is the one that goes first — every other
    unknown cost or price is downstream of it, propagating exactly like a
    missing component rate already does (see the module docstring). A
    consumer that reads `effective_multiplier is None` must use
    `price_per_min` directly instead of scaling the cost breakdown by it —
    there is no multiplier that turns a zero stack into a non-zero price.
    """

    model_id: str
    is_audio: bool
    cost_per_min_model: float | None
    cost_per_min_stack: float | None
    price_per_min: float | None
    rule_source: str
    effective_multiplier: float | None
    input_per_1m_cost: float
    output_per_1m_cost: float


def current_rows(model: Any, key_column: Any, at_ms: int | ColumnElement[Any]) -> Any:
    """The row in force at `at_ms` for each key.

    Public because effective-dating is not just this module's business: any
    consumer that reads a rate table alongside these prices has to select the
    same row, or it will serve a future-dated rate as today's while the
    headline price still reflects the old one.

    row_number() over a descending effective_from_ms, rather than a max()
    subquery join: it keeps the whole row without a self-join and renders on
    both dialects. `id` breaks the tie after the timestamp — the unique
    constraint on (key, effective_from_ms) makes a tie unreachable today, so
    this is not correcting for duplicate rows; it is so the select stays
    deterministic (and the view keeps matching the API) if that constraint is
    ever relaxed or a key is ever partitioned differently.
    """
    ranked = (
        select(
            model,
            func.row_number()
            .over(
                partition_by=key_column,
                order_by=(model.effective_from_ms.desc(), model.id.desc()),
            )
            .label("rn"),
        )
        .where(model.effective_from_ms <= at_ms)
        .subquery()
    )
    return select(ranked).where(ranked.c.rn == 1).subquery()


def assumption(key: str) -> ColumnElement[Any]:
    """One `pricing_assumptions` value, as a subquery rather than a number.

    Reading these into Python and folding them into the arithmetic would bake
    them into the compiled view as literals, so an edited assumption reached
    the API on the next request and Grafana only on the next API restart —
    two dashboards describing the same call differently in between. A scalar
    subquery re-evaluates per query, exactly as `at_ms` already does, and
    renders on SQLite as well as Postgres so the API keeps executing the same
    Select the view is compiled from.

    A missing key reads as NULL and propagates: the model's cost, and so its
    price, become unknown. That is the module's standing rule (see the module
    docstring) and the right one here too — a deleted assumption must not
    quietly re-price the catalog off a coalesced zero.
    """
    return select(PricingAssumption.value).where(PricingAssumption.key == key).scalar_subquery()


class PriceSources(NamedTuple):
    """Where the arithmetic in `price_columns` reads its inputs.

    Two implementations exist. `relation_sources` reads the rate card as
    joined relations -- one row per model, all priced at one instant, which is
    the shape `pricing.model_price` is built from. `scalar_sources` reads it as
    correlated scalar subqueries, so each row of an outer query -- a call --
    can be priced at its own instant. Only the lookups differ; the formula must
    not, which is the whole reason this indirection exists.
    """

    is_audio: ColumnElement[Any]
    input_per_1m: ColumnElement[Any]
    output_per_1m: ColumnElement[Any]
    component: Callable[[str], ColumnElement[Any]]
    model_rule: Callable[[Any], ColumnElement[Any]]
    global_rule: Callable[[Any], ColumnElement[Any]]


class PriceColumns(NamedTuple):
    """Unlabeled expressions. Callers label them to suit their select."""

    cost_per_min_model: ColumnElement[Any]
    cost_per_min_stack: ColumnElement[Any]
    price_per_min: ColumnElement[Any]
    rule_source: ColumnElement[Any]


def price_columns(sources: PriceSources) -> PriceColumns:
    """The one cost -> price formula. See the module docstring."""
    tokens_per_min = assumption("audio_tokens_per_sec") * 60.0
    talk = assumption("agent_talk_ratio")
    turns = assumption("turns_per_min")
    out_tokens = assumption("output_tokens_per_turn")
    in_tokens = assumption("display_input_tokens_per_turn")

    # No coalesce to 0: a renamed or expired component would then shave ~87%
    # off a text minute's cost with no signal at all. NULL propagates through
    # the sum, so a missing rate makes the whole stack cost unknown instead.
    stt = sources.component("cartesia_stt")
    tts = sources.component("cartesia_tts")

    # Audio models bill the audio stream; text models bill turns. Two formulas,
    # not two rates — applying the text formula to a Live model under-prices an
    # audio minute by roughly 6x.
    audio_cost = cast(
        tokens_per_min / 1e6 * sources.input_per_1m
        + talk * tokens_per_min / 1e6 * sources.output_per_1m,
        Float,
    )
    text_cost = cast(
        turns * (in_tokens / 1e6 * sources.input_per_1m + out_tokens / 1e6 * sources.output_per_1m),
        Float,
    )
    cost_model = case((sources.is_audio, audio_cost), else_=text_cost)
    # Live replaces STT+LLM+TTS with one model, so it has no synthesis leg —
    # and so a missing STT/TTS rate leaves an audio model's cost known.
    cost_stack = case((sources.is_audio, cost_model), else_=cost_model + stt + tts)

    def marked_up(markup: ColumnElement[Any], fixed: ColumnElement[Any]) -> ColumnElement[Any]:
        return cost_stack * (1.0 + func.coalesce(markup, 0.0) / 100.0) + func.coalesce(fixed, 0.0)

    model_explicit = sources.model_rule(PriceRule.explicit_per_minute_usd)
    model_markup = sources.model_rule(PriceRule.markup_pct)
    model_fixed = sources.model_rule(PriceRule.fixed_per_minute_usd)
    global_explicit = sources.global_rule(PriceRule.explicit_per_minute_usd)
    global_markup = sources.global_rule(PriceRule.markup_pct)
    global_fixed = sources.global_rule(PriceRule.fixed_per_minute_usd)

    # Within a scope: an explicit price wins, otherwise markup/fixed. A rule
    # row that sets none of the three is not a price — it is an empty row, so
    # the search continues to the next scope rather than pretending a 0%
    # markup was intended. The rule's id is deliberately not probed for that
    # reason: existence is not a price.
    model_has_derived = or_(model_markup.isnot(None), model_fixed.isnot(None))
    global_has_derived = or_(global_markup.isnot(None), global_fixed.isnot(None))

    # Falling through to `cost_stack` here is what silently sells at cost, so
    # the last branch is NULL. An operator who deletes the global rule gets a
    # blank price, which is visible; a price equal to cost is not.
    price = case(
        (model_explicit.isnot(None), model_explicit),
        (model_has_derived, marked_up(model_markup, model_fixed)),
        (global_explicit.isnot(None), global_explicit),
        (global_has_derived, marked_up(global_markup, global_fixed)),
        else_=null(),
    )
    rule_source = case(
        (model_explicit.isnot(None), literal("explicit")),
        (model_has_derived, literal("model")),
        # A flat platform price is "explicit" whichever scope carries it: the
        # signal a reader needs is that cost did not enter into it.
        (global_explicit.isnot(None), literal("explicit")),
        (global_has_derived, literal("global")),
        else_=literal("none"),
    )
    return PriceColumns(cost_model, cost_stack, price, rule_source)


def relation_sources(rates: Any, rules: Any, components: Any) -> PriceSources:
    """Read the rate card as joined relations: one row per model, one instant.

    This is the shape `pricing.model_price` is built from.
    """

    def component(name: str) -> ColumnElement[Any]:
        return (
            select(components.c.unit_price_usd)
            .where(components.c.component == name)
            .scalar_subquery()
        )

    # A correlated scalar per column instead of one LATERAL join: LATERAL is
    # Postgres-only, and the tests — like any future SQLite consumer — must see
    # the same arithmetic the view will compute.
    def model_rule(column: Any) -> ColumnElement[Any]:
        return (
            select(rules.c[column.key])
            .where(rules.c.scope == rates.c.model_id)
            .correlate(rates)
            .scalar_subquery()
        )

    def global_rule(column: Any) -> ColumnElement[Any]:
        return select(rules.c[column.key]).where(rules.c.scope == "*").scalar_subquery()

    return PriceSources(
        is_audio=rates.c.is_audio,
        input_per_1m=rates.c.input_per_1m_usd,
        output_per_1m=rates.c.output_per_1m_usd,
        component=component,
        model_rule=model_rule,
        global_rule=global_rule,
    )


def _latest(
    column: Any, key_column: Any, key: Any, effective: Any, at_ms: Any
) -> ColumnElement[Any]:
    """The value of `column` on the row in force at `at_ms`.

    ORDER BY ... LIMIT 1 inside a scalar subquery, which correlates to an outer
    row without LATERAL and renders identically on both dialects. This is the
    effective-dating `current_rows` does with a window function, in the shape a
    per-row lookup needs.
    """
    return (
        select(column)
        .where(key_column == key, effective <= at_ms)
        .order_by(effective.desc())
        .limit(1)
        .scalar_subquery()
    )


def scalar_sources(model_id: ColumnElement[Any], at_ms: ColumnElement[Any]) -> PriceSources:
    """Read the rate card by correlated lookup, effective-dated per outer row.

    `model_id` and `at_ms` are expressions from the calling query — typically a
    resolved model and a call's `start_timestamp` — so each call is priced with
    the rates and rules that were in force when it ran, rather than with
    today's rate card applied backwards over history.

    One limit this does not remove: `pricing_assumptions` carries no effective
    dating at all, so an edited assumption still moves every historical price.
    """
    return PriceSources(
        is_audio=_latest(
            ModelCostRate.is_audio,
            ModelCostRate.model_id,
            model_id,
            ModelCostRate.effective_from_ms,
            at_ms,
        ),
        input_per_1m=_latest(
            ModelCostRate.input_per_1m_usd,
            ModelCostRate.model_id,
            model_id,
            ModelCostRate.effective_from_ms,
            at_ms,
        ),
        output_per_1m=_latest(
            ModelCostRate.output_per_1m_usd,
            ModelCostRate.model_id,
            model_id,
            ModelCostRate.effective_from_ms,
            at_ms,
        ),
        component=lambda name: _latest(
            CostRate.unit_price_usd,
            CostRate.component,
            name,
            CostRate.effective_from_ms,
            at_ms,
        ),
        model_rule=lambda column: _latest(
            column, PriceRule.scope, model_id, PriceRule.effective_from_ms, at_ms
        ),
        global_rule=lambda column: _latest(
            column, PriceRule.scope, literal("*"), PriceRule.effective_from_ms, at_ms
        ),
    )


def model_price_select(at_ms: int | ColumnElement[Any]) -> Select[Any]:
    """Build the cost -> price select.

    `at_ms` is normally an int (a request's "as of" instant). The
    pricing_view module instead passes a SQL expression — a Postgres now()
    derivation — so that when this select is compiled into the
    `pricing.model_price` view, the view re-evaluates "in force now" on every
    query rather than freezing whichever rate happened to be current at
    CREATE VIEW time.
    """
    rates = current_rows(ModelCostRate, ModelCostRate.model_id, at_ms)
    rules = current_rows(PriceRule, PriceRule.scope, at_ms)
    components = current_rows(CostRate, CostRate.component, at_ms)
    cols = price_columns(relation_sources(rates, rules, components))

    return select(
        rates.c.model_id.label("model_id"),
        rates.c.is_audio.label("is_audio"),
        cols.cost_per_min_model.label("cost_per_min_model"),
        cols.cost_per_min_stack.label("cost_per_min_stack"),
        cols.price_per_min.label("price_per_min"),
        cols.rule_source.label("rule_source"),
        rates.c.input_per_1m_usd.label("input_per_1m_cost"),
        rates.c.output_per_1m_usd.label("output_per_1m_cost"),
    ).select_from(rates)


async def model_prices(session: AsyncSession, at_ms: int | None = None) -> list[ModelPrice]:
    at = now_ms() if at_ms is None else at_ms
    rows = (await session.execute(model_price_select(at))).all()
    out: list[ModelPrice] = []
    for row in rows:
        # A missing pricing_assumptions row makes the arithmetic in
        # model_price_select() (tokens_per_min, talk, turns, ...) NULL, which
        # makes cost_per_min_model itself NULL — not just the stack or the
        # price built on top of it. float(None) would raise TypeError here
        # and turn a deleted assumption into a 500 from the whole pricing
        # endpoint instead of the "unknown, never free" degrade every other
        # missing input in this module already gets.
        model_cost = None if row.cost_per_min_model is None else float(row.cost_per_min_model)
        stack = None if row.cost_per_min_stack is None else float(row.cost_per_min_stack)
        price = None if row.price_per_min is None else float(row.price_per_min)
        out.append(
            ModelPrice(
                model_id=row.model_id,
                is_audio=bool(row.is_audio),
                cost_per_min_model=model_cost,
                cost_per_min_stack=stack,
                price_per_min=price,
                rule_source=row.rule_source,
                # The ratio the frontend multiplies every breakdown row by, so
                # the rows always sum to the headline price — true even for an
                # explicit price or a fixed adder, neither of which can be
                # expressed as a per-token markup. It is None, not 1.0, when
                # there is nothing to scale (zero or unknown cost) or nothing
                # to scale to (no price): consumers must then show
                # `price_per_min` directly rather than a fabricated ratio.
                effective_multiplier=(price / stack) if price is not None and stack else None,
                input_per_1m_cost=float(row.input_per_1m_cost),
                output_per_1m_cost=float(row.output_per_1m_cost),
            )
        )
    return out
