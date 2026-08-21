# Pricing Domain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate what a call costs us from what a customer pays, derive the second from the first by an explicit rule, and stop the product quoting at cost.

**Architecture:** Four effective-dated tables hold costs, price rules and shared assumptions. The rule that turns cost into price is written **once**, as a SQLAlchemy `Select` in `services/pricing.py`. The API executes that select directly; on Postgres, boot compiles the same select into a `pricing.model_price` view for Grafana. A new dashboard endpoint serves prices only — never costs — and the frontend estimator consumes it with a compiled-in fallback.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2 async, Postgres 16 (prod) / SQLite (tests), pytest; Next.js 15 + TypeScript + bun for the frontend.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-20-pricing-domain-design.md`. Read it before Task 1.
- **No cost value may ever leave the API.** The response model has no field for one. Task 4 enforces this with a test.
- Schema changes ship via `Base.metadata.create_all` + idempotent boot statements in `main.py`. This repo has **no Alembic** — do not add it.
- Money columns use the `MONEY` type from Task 1: `Numeric(12, 6)` on Postgres, `Float` on SQLite. Never bare `Numeric` (SQLite raises `SAWarning` and the suite runs on SQLite).
- Seeds insert **only when the table is empty**, and must survive four API replicas booting at once. Every seeded table carries a unique constraint and seeding tolerates `IntegrityError`.
- Backend tests: `cd backend && uv run pytest`. Frontend: `cd frontend && bun test`.
- Commit messages are conventional commits (`feat:`, `fix:`, `docs:`, `test:`). `main` is protected — work on a branch.
- Prices in seeds come from the spec's tables verbatim. Do not round, re-derive, or "improve" them.

---

### Task 1: Pricing tables and seeds

**Files:**
- Modify: `backend/src/arhiteq_api/models.py` (append after `TestCaseJob`, end of file)
- Create: `backend/src/arhiteq_api/services/pricing_seed.py`
- Modify: `backend/src/arhiteq_api/main.py:118-126` (lifespan)
- Test: `backend/tests/unit/test_pricing_seed.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MONEY` type alias; models `ModelCostRate`, `CostRate`, `PriceRule`, `PricingAssumption`; `async def seed_pricing_defaults(session: AsyncSession) -> None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_pricing_seed.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_pricing_seed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.pricing_seed'`

- [ ] **Step 3: Add the models**

Append to `backend/src/arhiteq_api/models.py`. Add `Numeric` to the `sqlalchemy` import block at the top of the file first:

```python
# Exact decimal in Postgres; SQLite has no numeric type and SQLAlchemy warns
# on every Decimal round-trip, so the test dialect gets a float. Prices are
# summed across thousands of call rows and compared against provider invoices,
# which is why prod stays exact.
MONEY = Numeric(12, 6).with_variant(Float(), "sqlite")


class ModelCostRate(Base):
    """What one model's tokens cost us. Mirrors LLM_RATES in estimates.ts."""

    __tablename__ = "model_cost_rates"
    __table_args__ = (UniqueConstraint("model_id", "effective_from_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(String(64), index=True)
    input_per_1m_usd: Mapped[float] = mapped_column(MONEY)
    output_per_1m_usd: Mapped[float] = mapped_column(MONEY)
    # Live models bill audio tokens (25/sec), not text turns — a different cost
    # formula entirely, not a different rate.
    is_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    effective_from_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(String(255))


class CostRate(Base):
    """Costs not priced per token: STT, TTS, telephony, fixed infrastructure."""

    __tablename__ = "cost_rates"
    __table_args__ = (UniqueConstraint("component", "effective_from_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(64), index=True)
    unit: Mapped[str] = mapped_column(String(16))  # per_minute | per_call | per_month
    unit_price_usd: Mapped[float] = mapped_column(MONEY)
    effective_from_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(String(255))


class PriceRule(Base):
    """How a customer price is derived from a cost.

    Evaluated explicit > per-model > global; see services/pricing.py, which is
    the only place that ordering is implemented.
    """

    __tablename__ = "price_rules"
    __table_args__ = (UniqueConstraint("scope", "effective_from_ms"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), index=True)  # model_id | "*"
    explicit_per_minute_usd: Mapped[float | None] = mapped_column(MONEY)
    markup_pct: Mapped[float | None] = mapped_column(MONEY)
    fixed_per_minute_usd: Mapped[float | None] = mapped_column(MONEY)
    effective_from_ms: Mapped[int] = mapped_column(BigInteger, default=0)
    note: Mapped[str | None] = mapped_column(String(255))


class PricingAssumption(Base):
    """Constants the SQL price view and estimates.ts must agree on.

    They live in a table because the alternative is a copy in Python and a copy
    in TypeScript, which lets the model picker and the cost estimate disagree
    while both look correct.
    """

    __tablename__ = "pricing_assumptions"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(255))
```

- [ ] **Step 4: Write the seed module**

Create `backend/src/arhiteq_api/services/pricing_seed.py`:

```python
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
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_pricing_seed.py -v`
Expected: 4 passed

- [ ] **Step 6: Call the seed at boot**

In `backend/src/arhiteq_api/main.py`, inside `lifespan`, after the existing `create_all` / `_apply_column_backfills` block, add:

```python
    async with session_factory()() as session:
        await seed_pricing_defaults(session)
```

Add the imports at the top of `main.py`:

```python
from .db import get_engine, session_factory
from .services.pricing_seed import seed_pricing_defaults
```

(`get_engine` is already imported; extend the existing line rather than duplicating it.)

- [ ] **Step 7: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass. If `test_settings.py` or contract tests fail, the seed is running where it should not — check that it is inside `lifespan` and not at import time.

- [ ] **Step 8: Commit**

```bash
git add backend/src/arhiteq_api/models.py backend/src/arhiteq_api/services/pricing_seed.py \
        backend/src/arhiteq_api/main.py backend/tests/unit/test_pricing_seed.py
git commit -m "feat(pricing): cost, price-rule and assumption tables with first-boot seeds"
```

---

### Task 2: The price rule, written once

**Files:**
- Create: `backend/src/arhiteq_api/services/pricing.py`
- Test: `backend/tests/unit/test_pricing.py`

**Interfaces:**
- Consumes: `ModelCostRate`, `CostRate`, `PriceRule`, `PricingAssumption` from Task 1.
- Produces:
  - `def model_price_select(assumptions: dict[str, float], at_ms: int) -> Select`
  - `async def load_assumptions(session: AsyncSession) -> dict[str, float]`
  - `async def model_prices(session: AsyncSession, at_ms: int | None = None) -> list[ModelPrice]`
  - `class ModelPrice(NamedTuple)` with fields `model_id: str`, `is_audio: bool`, `cost_per_min_model: float`, `cost_per_min_stack: float`, `price_per_min: float`, `rule_source: str`, `effective_multiplier: float`, `input_per_1m_cost: float`, `output_per_1m_cost: float`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_pricing.py`:

```python
import pytest
from sqlalchemy import select

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_pricing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.pricing'`

- [ ] **Step 3: Implement the rule**

Create `backend/src/arhiteq_api/services/pricing.py`:

```python
"""The one implementation of cost -> price.

Both the pricing endpoint and the Grafana margin panels read this. Written
twice — once in Python for the API, once in SQL for the dashboard — the two
would drift, and the day they disagree the price list and the margin report
describe the same call differently. So it is one SQLAlchemy Select: the API
executes it, and boot compiles it into the `pricing.model_price` view.
"""

from typing import NamedTuple

from sqlalchemy import Float, Select, and_, case, cast, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

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


def _current(model, key_column, at_ms: int):
    """The row in force at `at_ms` for each key.

    row_number() over a descending effective_from_ms, rather than a max()
    subquery join: the latter duplicates a key that has two rows with the same
    timestamp, which is exactly what a mistaken double-insert produces.
    """
    ranked = select(
        model,
        func.row_number()
        .over(partition_by=key_column, order_by=model.effective_from_ms.desc())
        .label("rn"),
    ).where(model.effective_from_ms <= at_ms).subquery()
    return select(ranked).where(ranked.c.rn == 1).subquery()


def model_price_select(assumptions: dict[str, float], at_ms: int) -> Select:
    tokens_per_min = assumptions["audio_tokens_per_sec"] * 60.0
    talk = assumptions["agent_talk_ratio"]
    turns = assumptions["turns_per_min"]
    out_tokens = assumptions["output_tokens_per_turn"]
    in_tokens = assumptions["display_input_tokens_per_turn"]

    rates = _current(ModelCostRate, ModelCostRate.model_id, at_ms)
    rules = _current(PriceRule, PriceRule.scope, at_ms)
    components = _current(CostRate, CostRate.component, at_ms)

    def component(name: str):
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

    model_rule = (
        select(rules)
        .where(rules.c.scope == rates.c.model_id)
        .subquery()
        .lateral()
    )
    global_rule = select(rules).where(rules.c.scope == "*").subquery()

    def marked_up(rule, cost):
        return cost * (1.0 + func.coalesce(rule.c.markup_pct, 0.0) / 100.0) + func.coalesce(
            rule.c.fixed_per_minute_usd, 0.0
        )

    price = func.coalesce(
        model_rule.c.explicit_per_minute_usd,
        case(
            (
                model_rule.c.id.isnot(None),
                marked_up(model_rule, cost_stack),
            ),
            else_=None,
        ),
        marked_up(global_rule, cost_stack),
    )
    rule_source = case(
        (model_rule.c.explicit_per_minute_usd.isnot(None), literal("explicit")),
        (model_rule.c.id.isnot(None), literal("model")),
        else_=literal("global"),
    )

    return (
        select(
            rates.c.model_id.label("model_id"),
            rates.c.is_audio.label("is_audio"),
            cost_model.label("cost_per_min_model"),
            cost_stack.label("cost_per_min_stack"),
            price.label("price_per_min"),
            rule_source.label("rule_source"),
            rates.c.input_per_1m_usd.label("input_per_1m_cost"),
            rates.c.output_per_1m_usd.label("output_per_1m_cost"),
        )
        .select_from(rates)
        .outerjoin(model_rule, and_(True))
        .join(global_rule, and_(True), isouter=True)
    )


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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_pricing.py -v`
Expected: 9 passed.

If the lateral join fails on SQLite (`LATERAL` is Postgres-only), replace the `model_rule` subquery with a correlated scalar lookup per column — `select(rules.c.markup_pct).where(rules.c.scope == rates.c.model_id).scalar_subquery()` — and rebuild `price`/`rule_source` from those scalars. Do not branch on dialect: one expression must serve both databases, or the view and the API stop matching.

- [ ] **Step 5: Commit**

```bash
git add backend/src/arhiteq_api/services/pricing.py backend/tests/unit/test_pricing.py
git commit -m "feat(pricing): evaluate explicit > model > global price rules in one select"
```

---

### Task 3: `pricing.model_price` view for Grafana

**Files:**
- Modify: `backend/src/arhiteq_api/main.py` (lifespan, after the Task 1 seed call)
- Create: `backend/src/arhiteq_api/services/pricing_view.py`
- Test: `backend/tests/unit/test_pricing_view.py`

**Interfaces:**
- Consumes: `model_price_select`, `load_assumptions` from Task 2.
- Produces: `async def apply_pricing_view(session: AsyncSession) -> bool` — returns `True` when the view was created, `False` on a dialect without schemas (SQLite).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_pricing_view.py`:

```python
from sqlalchemy import text

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

    assert "1500.0" in sql  # 25 tokens/sec x 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_pricing_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'arhiteq_api.services.pricing_view'`

- [ ] **Step 3: Implement the renderer**

Create `backend/src/arhiteq_api/services/pricing_view.py`:

```python
"""Expose the price rule to Grafana as a Postgres view.

Grafana reads SQL, not Python, so the rule has to exist in the database. It is
not written a second time: the same Select from services/pricing.py is compiled
to Postgres and wrapped in CREATE OR REPLACE VIEW, so the view and the endpoint
cannot disagree.

Grafana's role gets SELECT on this view and on nothing underneath it, so the
cost tables stay unreachable while the dashboard still computes margin.
"""

from sqlalchemy import text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import now_ms
from .pricing import load_assumptions, model_price_select

VIEW_NAME = "pricing.model_price"


async def render_pricing_view_sql(session: AsyncSession) -> str:
    assumptions = await load_assumptions(session)
    # at_ms = 0 would freeze the view at the epoch. The view must re-evaluate
    # "in force now" on every query, so the timestamp comparison is rendered
    # against a SQL now() rather than a Python constant.
    stmt = model_price_select(assumptions, at_ms=now_ms())
    compiled = stmt.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    body = str(compiled).replace(str(now_ms()), "(EXTRACT(EPOCH FROM now()) * 1000)::bigint")
    return f"CREATE OR REPLACE VIEW {VIEW_NAME} AS {body}"


async def apply_pricing_view(session: AsyncSession) -> bool:
    if session.bind.dialect.name != "postgresql":
        return False
    await session.execute(text("CREATE SCHEMA IF NOT EXISTS pricing"))
    await session.execute(text(await render_pricing_view_sql(session)))
    await session.commit()
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_pricing_view.py -v`
Expected: 3 passed.

- [ ] **Step 5: Call it at boot**

In `main.py`'s `lifespan`, immediately after `await seed_pricing_defaults(session)`:

```python
        await apply_pricing_view(session)
```

with `from .services.pricing_view import apply_pricing_view` at the top.

- [ ] **Step 6: Verify against a real Postgres**

Run:

```bash
cd backend && docker compose -f ../docker-compose.yml up -d postgres && \
  ARHITEQ_DATABASE_URL=postgresql+asyncpg://arhiteq:arhiteq@localhost:5432/arhiteq \
  uv run python -c "
import asyncio
from arhiteq_api.db import session_factory, get_engine
from arhiteq_api.models import Base
from arhiteq_api.services.pricing_seed import seed_pricing_defaults
from arhiteq_api.services.pricing_view import apply_pricing_view
from sqlalchemy import text

async def main():
    async with get_engine().begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with session_factory()() as s:
        await seed_pricing_defaults(s)
        print('view created:', await apply_pricing_view(s))
        for r in (await s.execute(text('select model_id, cost_per_min_stack, price_per_min, rule_source from pricing.model_price order by 1'))).all():
            print(r)
asyncio.run(main())"
```

Expected: `view created: True`, then seven rows, each with `rule_source = global` and `price_per_min ≈ cost_per_min_stack × 4`.

Stop the container when done: `docker compose -f ../docker-compose.yml down`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/arhiteq_api/services/pricing_view.py \
        backend/tests/unit/test_pricing_view.py backend/src/arhiteq_api/main.py
git commit -m "feat(pricing): compile the price rule into a Postgres view for Grafana"
```

---

### Task 4: `GET /dashboard/pricing/models`

**Files:**
- Modify: `backend/src/arhiteq_api/api/dashboard.py` (append a new section at the end)
- Test: `backend/tests/unit/test_pricing_endpoint.py`

**Interfaces:**
- Consumes: `model_prices`, `load_assumptions` from Task 2; `require_api_key`, `get_session` already imported in `dashboard.py`.
- Produces: `GET /dashboard/pricing/models` returning `{assumptions: {...}, models: [...], components: {...}}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_pricing_endpoint.py`:

```python
from tests.conftest import AUTH_HEADERS

LIVE = "gemini-live-2.5-flash-native-audio"


async def test_returns_a_price_for_every_catalog_model(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    assert {m["model_id"] for m in body["models"]} == {
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-3.1-flash-live-preview",
        LIVE,
    }


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


async def test_returns_the_assumptions_the_view_used(client):
    body = (await client.get("/dashboard/pricing/models", headers=AUTH_HEADERS)).json()
    assert body["assumptions"]["audio_tokens_per_sec"] == 25.0
    assert body["assumptions"]["agent_talk_ratio"] == 0.5


async def test_requires_authentication(client):
    assert (await client.get("/dashboard/pricing/models")).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_pricing_endpoint.py -v`
Expected: FAIL — 404 on every request.

- [ ] **Step 3: Implement the endpoint**

Append to `backend/src/arhiteq_api/api/dashboard.py`:

```python
# --------------------------------------------------------------- pricing
# List prices for the agent editor. PRICES ONLY — the response model has no
# field a cost could occupy, so leaking one would take a deliberate schema
# change rather than a slip.

_PRICED_COMPONENTS = ("cartesia_stt", "cartesia_tts", "kb_overhead")


@router.get("/dashboard/pricing/models")
async def pricing_models(
    api_key: ApiKey = Depends(require_api_key),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    assumptions = await load_assumptions(session)
    prices = await model_prices(session)

    component_costs = {
        row.component: float(row.unit_price_usd)
        for row in (
            await session.execute(
                select(CostRate).where(CostRate.component.in_(_PRICED_COMPONENTS))
            )
        ).scalars()
    }
    # STT and TTS are shared by every text model, so they cannot carry a
    # per-model multiplier — they get the multiplier of a model priced by the
    # global rule. The frontend never sums these into the headline: it derives
    # the LLM row as (headline - components), so the breakdown adds up exactly
    # whatever rule priced the model. Averaging the per-model multipliers here
    # would look reasonable and make the rows stop adding up the moment one
    # model is priced differently.
    global_priced = next((p for p in prices if p.rule_source == "global"), None)
    multiplier = global_priced.effective_multiplier if global_priced else 1.0

    return {
        "assumptions": assumptions,
        "models": [
            {
                "model_id": p.model_id,
                "is_audio": p.is_audio,
                # Marked up so the estimator's existing token math operates on
                # prices without changing shape.
                "input_per_1m": p.input_per_1m_cost * p.effective_multiplier,
                "output_per_1m": p.output_per_1m_cost * p.effective_multiplier,
                "per_minute": p.price_per_min,
                # A fixed adder cannot be folded into a per-token rate, so it
                # rides separately and is added after the token math.
                "per_minute_adder": 0.0,
            }
            for p in prices
        ],
        "components": {
            name: value * multiplier for name, value in component_costs.items()
        },
    }
```

Extend the existing imports at the top of `dashboard.py`:

```python
from ..models import (
    ...,
    CostRate,
)
from ..services.pricing import load_assumptions, model_prices
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_pricing_endpoint.py -v`
Expected: 5 passed.

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all pass, including `tests/contract/` — this endpoint is additive and must not alter the Retell surface.

- [ ] **Step 6: Commit**

```bash
git add backend/src/arhiteq_api/api/dashboard.py backend/tests/unit/test_pricing_endpoint.py
git commit -m "feat(pricing): serve list prices to the dashboard, never costs"
```

---

### Task 5: Frontend consumes prices, with a fallback

**Files:**
- Modify: `frontend/src/lib/estimates.ts:176-262` (rate constants → injected price card)
- Modify: `frontend/src/lib/api.ts` (add `getPricing` to the `api` object)
- Modify: `frontend/src/lib/types.ts` (add `PriceCard`)
- Test: `frontend/src/lib/__tests__/estimates.test.ts` (extend)

**Interfaces:**
- Consumes: `GET /dashboard/pricing/models` from Task 4.
- Produces:
  - `export interface PriceCard { assumptions: Record<string, number>; models: PricedModel[]; components: Record<string, number> }`
  - `export const FALLBACK_PRICES: PriceCard`
  - `estimateCost(input, tokens, prices: PriceCard)` and `llmDisplayCostPerMin(model, prices: PriceCard)` — both gain a required third/second parameter.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/lib/__tests__/estimates.test.ts`:

```ts
import { FALLBACK_PRICES, estimateCost, llmDisplayCostPerMin } from "@/lib/estimates";

const LIVE = "gemini-live-2.5-flash-native-audio";

describe("price card", () => {
  it("quotes above cost for every model in the fallback", () => {
    // A stale fallback must never quote below cost — it is what renders when
    // the pricing endpoint is unreachable.
    const COSTS: Record<string, number> = {
      "gemini-live-2.5-flash-native-audio": 0.0135,
      "gemini-3.1-flash-live-preview": 0.0135,
    };
    for (const [model, cost] of Object.entries(COSTS)) {
      expect(llmDisplayCostPerMin(model, FALLBACK_PRICES)).toBeGreaterThan(cost);
    }
  });

  it("uses the supplied price card rather than the fallback", () => {
    const doubled: typeof FALLBACK_PRICES = {
      ...FALLBACK_PRICES,
      models: FALLBACK_PRICES.models.map((m) => ({ ...m, per_minute: m.per_minute * 2 })),
    };
    expect(llmDisplayCostPerMin(LIVE, doubled)).toBeCloseTo(
      llmDisplayCostPerMin(LIVE, FALLBACK_PRICES) * 2,
    );
  });

  it("falls back for a model missing from the card", () => {
    const empty = { ...FALLBACK_PRICES, models: [] };
    expect(llmDisplayCostPerMin(LIVE, empty)).toBeGreaterThan(0);
  });

  it("breaks the headline down into rows that sum back to it", () => {
    const input = llmEstimateInput({ llm_id: "llm_1", model: LIVE } as RawLlm);
    const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);
    expect(cost.max).toBeCloseTo(llmDisplayCostPerMin(LIVE, FALLBACK_PRICES), 6);
  });

  it("adds the fixed per-minute adder after the token math", () => {
    const withAdder: typeof FALLBACK_PRICES = {
      ...FALLBACK_PRICES,
      models: FALLBACK_PRICES.models.map((m) =>
        m.model_id === LIVE ? { ...m, per_minute_adder: 0.05 } : m,
      ),
    };
    expect(llmDisplayCostPerMin(LIVE, withAdder)).toBeCloseTo(
      llmDisplayCostPerMin(LIVE, FALLBACK_PRICES) + 0.05,
    );
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && bun test src/lib/__tests__/estimates.test.ts`
Expected: FAIL — `FALLBACK_PRICES` is not exported.

- [ ] **Step 3: Add the types and the API call**

In `frontend/src/lib/types.ts`:

```ts
export interface PricedModel {
  model_id: string;
  is_audio: boolean;
  input_per_1m: number;
  output_per_1m: number;
  per_minute: number;
  per_minute_adder: number;
}

export interface PriceCard {
  assumptions: Record<string, number>;
  models: PricedModel[];
  components: Record<string, number>;
}
```

In `frontend/src/lib/api.ts`, inside the exported `api` object:

```ts
  getPricing: () => request<PriceCard>("/dashboard/pricing/models"),
```

`request<T>` is the module-local helper defined at `frontend/src/lib/api.ts:88`;
match the one-line style of its neighbours (`listVoices`, `getWorkspace`).

- [ ] **Step 4: Rework estimates.ts**

Replace the `LLM_RATES` constant and the `STT_COST_PER_MIN` / `TTS_COST_PER_MIN` / `INFRA_COST_PER_MIN` / `KB_COST_PER_MIN` constants with a fallback card and lookups:

```ts
/**
 * Last-known PRICES (not costs), compiled in so the editor still estimates
 * when the pricing endpoint is unreachable. Showing a zero, an error, or our
 * cost to a customer are all worse outcomes than showing a slightly stale
 * price — so the fallback is deliberately conservative.
 *
 * Refresh this whenever the markup rule changes. `satisfies` keeps the
 * compile-time guarantee the old LLM_RATES had: adding a model to the catalog
 * without a price is a build error, not a runtime surprise.
 */
export const FALLBACK_PRICES: PriceCard = {
  assumptions: {
    audio_tokens_per_sec: 25,
    agent_talk_ratio: 0.5,
    turns_per_min: 4,
    output_tokens_per_turn: 150,
    display_input_tokens_per_turn: 1500,
  },
  models: [
    { model_id: "gemini-3.5-flash", is_audio: false, input_per_1m: 6.0, output_per_1m: 36.0, per_minute: 0.0552, per_minute_adder: 0 },
    { model_id: "gemini-3.1-flash-lite", is_audio: false, input_per_1m: 1.0, output_per_1m: 6.0, per_minute: 0.0708, per_minute_adder: 0 },
    { model_id: "gemini-2.5-pro", is_audio: false, input_per_1m: 5.0, output_per_1m: 40.0, per_minute: 0.0948, per_minute_adder: 0 },
    { model_id: "gemini-2.5-flash", is_audio: false, input_per_1m: 1.2, output_per_1m: 10.0, per_minute: 0.0744, per_minute_adder: 0 },
    { model_id: "gemini-2.5-flash-lite", is_audio: false, input_per_1m: 0.4, output_per_1m: 1.6, per_minute: 0.0672, per_minute_adder: 0 },
    { model_id: "gemini-3.1-flash-live-preview", is_audio: true, input_per_1m: 12.0, output_per_1m: 48.0, per_minute: 0.054, per_minute_adder: 0 },
    { model_id: "gemini-live-2.5-flash-native-audio", is_audio: true, input_per_1m: 12.0, output_per_1m: 48.0, per_minute: 0.054, per_minute_adder: 0 },
  ],
  components: { cartesia_stt: 0.0088, cartesia_tts: 0.056, kb_overhead: 0.004 },
};

function priced(model: string, prices: PriceCard): PricedModel {
  return (
    prices.models.find((m) => m.model_id === model) ??
    FALLBACK_PRICES.models.find((m) => m.model_id === model) ??
    // Catalog drift: an imported or newer-than-the-catalog id. Fall back to a
    // Live price for a Live id — a text price would under-quote an audio
    // minute roughly 6x, and under-quoting is the expensive direction.
    (isLiveModel(model)
      ? FALLBACK_PRICES.models.find((m) => m.is_audio)!
      : FALLBACK_PRICES.models.find((m) => m.model_id === "gemini-2.5-flash")!)
  );
}
```

Then update the two exported functions to take the card:

```ts
export function llmDisplayCostPerMin(model: string, prices: PriceCard): number {
  const p = priced(model, prices);
  return p.per_minute + p.per_minute_adder;
}
```

In `estimateCost(input, tokens, prices)`, replace every component constant with
`prices.components.<name> ?? FALLBACK_PRICES.components.<name>`, and derive the
LLM row **from the headline minus the components** rather than from the token
math:

```ts
  // The rows must sum to the price the picker shows. Re-deriving the LLM row
  // from marked-up token rates would drift from `per_minute` whenever a model
  // carries an explicit price or a fixed adder, neither of which is expressible
  // as a per-token markup — and a breakdown that does not add up is worse than
  // no breakdown.
  const p = priced(input.model, prices);
  const headline = p.per_minute + p.per_minute_adder;
  const components = p.is_audio ? 0 : stt + tts;
  const llmRow = Math.max(headline - components - (input.hasKb ? kb : 0), 0);
```

Push `llmRow` as the `LLM: ${input.model}` (or `Gemini Live: ${input.model}`)
row and keep the component rows as they are. Keep every row label byte-for-byte
— they are user-visible copy.

The marked-up `input_per_1m` / `output_per_1m` stay in use for
`estimateTokens`' range display only.

Delete `INFRA_COST_PER_MIN` outright. It was priced from **LiveKit Cloud** while LiveKit is self-hosted on GKE, so it described a service we do not buy; real fixed infrastructure is now in `cost_rates.infra_fixed_monthly` and reaches the customer through the markup, not as a line item.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && bun test src/lib/__tests__/estimates.test.ts`
Expected: all pass, including the pre-existing estimate tests. Where an old test called `estimateCost(input, tokens)`, add `FALLBACK_PRICES` as the third argument.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/estimates.ts frontend/src/lib/api.ts frontend/src/lib/types.ts \
        frontend/src/lib/__tests__/estimates.test.ts
git commit -m "feat(pricing): estimate from a price card with a compiled-in fallback"
```

---

### Task 6: Wire the editor to the live price card

**Files:**
- Modify: `frontend/src/components/editor/LlmModelSelect.tsx:3` and its render
- Modify: `frontend/src/components/editor/MetaRow.tsx`
- Modify: `frontend/src/app/agents/[id]/page.tsx`
- Modify: `frontend/src/components/flow/NodePalette.tsx`
- Create: `frontend/src/lib/usePricing.ts`

**Interfaces:**
- Consumes: `api.getPricing`, `FALLBACK_PRICES`, `PriceCard` from Task 5.
- Produces: `export function usePricing(): PriceCard` — never null, never loading; returns the fallback until the fetch resolves.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/__tests__/usePricing.test.ts`:

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { usePricing } from "@/lib/usePricing";
import { FALLBACK_PRICES } from "@/lib/estimates";
import { api } from "@/lib/api";

describe("usePricing", () => {
  it("returns the fallback before the fetch resolves", () => {
    const { result } = renderHook(() => usePricing());
    expect(result.current).toBe(FALLBACK_PRICES);
  });

  it("keeps the fallback when the fetch fails", async () => {
    const spy = jest.spyOn(api, "getPricing").mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => usePricing());
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current).toBe(FALLBACK_PRICES);
    spy.mockRestore();
  });
});
```

The suite is **bun:test**, not jest — every file there starts
`import { describe, expect, test } from "bun:test";`. Use `spyOn` from
`bun:test` and `test(...)` rather than `it(...)`:

```ts
/// <reference types="bun" />
import { describe, expect, spyOn, test } from "bun:test";
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && bun test src/lib/__tests__/usePricing.test.ts`
Expected: FAIL — cannot resolve `@/lib/usePricing`.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/lib/usePricing.ts`:

```ts
import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { FALLBACK_PRICES } from "@/lib/estimates";
import type { PriceCard } from "@/lib/types";

/**
 * The current price card, with the compiled-in fallback as the initial value.
 *
 * Deliberately not `useApiData`: callers need a card on the very first render
 * to price the model picker, and a null-plus-loading shape would put a spinner
 * or a blank price where a number belongs. A stale price beats no price.
 */
export function usePricing(): PriceCard {
  const [card, setCard] = useState<PriceCard>(FALLBACK_PRICES);

  useEffect(() => {
    let live = true;
    api
      .getPricing()
      .then((fresh) => {
        if (live) setCard(fresh);
      })
      .catch(() => {
        // Keep the fallback. The editor must still estimate offline.
      });
    return () => {
      live = false;
    };
  }, []);

  return card;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && bun test src/lib/__tests__/usePricing.test.ts`
Expected: 2 passed.

- [ ] **Step 5: Thread the card through the four consumers**

In `LlmModelSelect.tsx`, call `const prices = usePricing();` and pass it: `llmDisplayCostPerMin(model.id, prices)`.

In `MetaRow.tsx`, `app/agents/[id]/page.tsx` and `flow/NodePalette.tsx`, do the same for every `estimateCost(...)` / `llmDisplayCostPerMin(...)` call site. Do not add a second `usePricing()` call inside a loop — hoist it to the component top.

- [ ] **Step 6: Verify the build and the full suite**

Run: `cd frontend && bun run build && bun test`
Expected: build succeeds with no type errors; all tests pass. A type error at a call site means a consumer was missed — fix it rather than widening the signature.

- [ ] **Step 7: Verify in the running app**

Run the local stack (`docker compose up -d`, then `make api` and `make web`), open an agent, and confirm the model picker shows prices roughly 4× the old figures — Gemini 2.5 Flash Live should read about `$0.054/min` rather than `$0.014/min`. Then stop the API and reload: the picker must still render prices from the fallback rather than blanks.

Stop the background services when finished.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/usePricing.ts frontend/src/lib/__tests__/usePricing.test.ts \
        frontend/src/components/editor/LlmModelSelect.tsx \
        frontend/src/components/editor/MetaRow.tsx \
        frontend/src/app/agents/\[id\]/page.tsx \
        frontend/src/components/flow/NodePalette.tsx
git commit -m "feat(pricing): quote list prices in the agent editor instead of cost"
```

---

### Task 7: Operator documentation

**Files:**
- Modify: `infra/README.md` (new section after § Grafana access)
- Modify: `docs/ARCHITECTURE.md` (one paragraph in the data-model section)

**Interfaces:**
- Consumes: everything above.
- Produces: no code.

- [ ] **Step 1: Write the operator section**

Add to `infra/README.md` after § Grafana access:

```markdown
### Pricing

Costs and prices live in Postgres, seeded on first boot and edited by hand:

| table | holds |
| --- | --- |
| `model_cost_rates` | per-model token costs (what we pay) |
| `cost_rates` | STT, TTS, telephony, fixed infrastructure |
| `price_rules` | how a price is derived: explicit > per-model > global |
| `pricing_assumptions` | constants shared by the SQL view and estimates.ts |

The seeded global rule is `markup_pct = 300` — **a placeholder**. Set the real
number before anyone sees a quote:

```sql
INSERT INTO price_rules (scope, markup_pct, effective_from_ms, note)
VALUES ('*', 250, (EXTRACT(EPOCH FROM now()) * 1000)::bigint, 'set 2026-08-21');
```

Insert, never update: rules are effective-dated so historical margin keeps the
price that was actually in force.

Check the result before it ships:

```sql
SELECT model_id, cost_per_min_stack, price_per_min, rule_source
FROM pricing.model_price ORDER BY 1;
```

`rule_source` says which rule produced each price — `explicit`, `model` or
`global`. Prices are compiled into the frontend as a fallback for when the API
is unreachable; after changing the markup, refresh `FALLBACK_PRICES` in
`frontend/src/lib/estimates.ts` to match.
```

- [ ] **Step 2: Note the domain in ARCHITECTURE.md**

Add one paragraph stating that cost and price are separate concerns, that the
price rule has a single implementation in `services/pricing.py` which is also
compiled into `pricing.model_price` for Grafana, and that the API serves prices
only.

- [ ] **Step 3: Commit**

```bash
git add infra/README.md docs/ARCHITECTURE.md
git commit -m "docs(pricing): operator runbook for the rate card and markup rule"
```

---

## Rollout after merge

1. Deploy the backend. Boot seeds the tables and creates the view; nothing customer-visible changes until Task 6 ships.
2. **Set the real markup** (`infra/README.md` § Pricing). Nobody else can make this call.
3. Verify with the `pricing.model_price` query above.
4. Deploy the frontend. The picker now quotes prices.
5. Refresh `FALLBACK_PRICES` to match the live card.

Steps 2 and 3 sit between the two deploys deliberately: the customer-visible change lands only after the numbers have been looked at.

## Follow-on

The metrics dashboard (`2026-08-20-business-metrics-dashboard-design.md`) reads `pricing.model_price` for the price side of every call, and gets its own plan once this one lands. `grafana_ro` will need `SELECT` on that view and on nothing beneath it.
