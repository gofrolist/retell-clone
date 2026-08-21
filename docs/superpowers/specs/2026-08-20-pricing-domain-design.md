# Pricing domain — design

Separate what a call *costs us* from what a customer *pays*, make the second
derive from the first by an explicit rule, and stop the product quoting at
cost.

## Why this exists

The agent editor's model picker shows a per-minute figure for every model:
`$0.014/min` for Gemini 2.5 Flash Live, `$0.002/min` for 3.1 Flash Lite. Those
numbers come from `frontend/src/lib/estimates.ts`, and they are **provider list
prices** — `$3.00/1M` audio in plus `$12.00/1M` audio out at 25 tokens/second
with a 50% talk ratio is exactly `$0.0135/min`. The module names its own
infrastructure line `INFRA_COST_PER_MIN`, because it was written as a cost
estimator. It then ended up in front of customers as a price list.

So the product currently quotes at cost, with no margin, and there is nowhere
in the system to express what margin should be.

For calibration: Retell publishes **$0.07–$0.31/minute** (voice infrastructure
$0.055, TTS $0.015, telephony $0.015, LLM $0.003–$0.16). Arhiteq's measured
cost for a Live phone minute is about **$0.018**. Retell's floor is roughly 4×
our cost.

## What this delivers

1. `cost_rates` and `model_cost_rates` — what we pay, internal only.
2. `price_rules` — how a price is derived from a cost.
3. `pricing.model_price` — one SQL view that evaluates the rule.
4. `GET /dashboard/pricing/models` — prices, never costs.
5. `estimates.ts` consuming that endpoint instead of its own constants.

## Non-goals

- **Billing.** No invoices, no payment, no per-workspace contract pricing.
  Prices here are list prices, identical for every workspace. A
  `workspace_price_override` table is a later, easy addition; designing it now
  without a customer to shape it is guesswork.
- **Metering.** Cost is still `duration × rate` with assumed talk ratios and
  turn counts. Replacing assumptions with measurements is its own spec.
- **Repricing the estimator's model.** The turn/token assumptions in
  `estimates.ts` are kept as they are; they move into a table so SQL and
  TypeScript stop keeping private copies, but their values do not change.

## Schema

Three tables, all effective-dated, all created by `create_all` and seeded only
when empty — the way `seed.py` handles first-boot data, so an operator edit
survives redeploys.

### `model_cost_rates`

Per-token costs, one row per model, mirroring the `LLM_RATES` constant it
replaces.

| column | type | note |
| --- | --- | --- |
| `model_id` | str(64) | matches the `LLM_MODELS` catalog |
| `input_per_1m_usd` | Numeric(12,6) | |
| `output_per_1m_usd` | Numeric(12,6) | |
| `is_audio` | bool | Live models bill audio tokens, not text turns |
| `effective_from_ms` | bigint | |
| `note` | str(255) | source and date read |

Seeded from the values already in `estimates.ts` (Google list prices, paid
tier, read 2026-07-14/31), because those are correct and sourced:

| model | in $/1M | out $/1M | audio |
| --- | --- | --- | --- |
| `gemini-3.5-flash` | 1.50 | 9.00 | no |
| `gemini-3.1-flash-lite` | 0.25 | 1.50 | no |
| `gemini-2.5-pro` | 1.25 | 10.00 | no |
| `gemini-2.5-flash` | 0.30 | 2.50 | no |
| `gemini-2.5-flash-lite` | 0.10 | 0.40 | no |
| `gemini-3.1-flash-live-preview` | 3.00 | 12.00 | yes |
| `gemini-live-2.5-flash-native-audio` | 3.00 | 12.00 | yes |

### `cost_rates`

Everything not priced per token.

| component | unit | USD | source |
| --- | --- | --- | --- |
| `cartesia_stt` | per_minute | 0.002200 | 1 credit/sec at $37.375/M credits |
| `cartesia_tts` | per_minute | 0.014000 | ~1 credit/char, ~750 chars/min of speech, 50% talk |
| `telnyx_outbound` | per_minute | 0.005000 | Elastic SIP, US local outbound |
| `telnyx_inbound` | per_minute | 0.003200 | Elastic SIP, US local inbound |
| `telnyx_did` | per_month | 2.000000 | 2 numbers × $1.00 |
| `kb_overhead` | per_minute | 0.001000 | embedding/retrieval, own estimate |
| `infra_fixed_monthly` | per_month | 1500.000000 | inventory below |

The Cartesia figures are the ones already in `estimates.ts` and are better
sourced than a tier division: they come from credit math against published
rates.

**`infra_fixed_monthly` replaces a wrong number.** `estimates.ts` carries
`INFRA_COST_PER_MIN = 0.001`, sourced from **LiveKit Cloud** pricing — but
LiveKit is self-hosted on GKE here, so that line describes a service we do not
buy. Real fixed infrastructure, priced from the GCP Cloud Billing catalog API
for `us-east1` on 2026-08-20:

| item | qty | rate | USD/mo |
| --- | --- | --- | --- |
| GKE regional cluster fee | 1 | $0.10/hr | 73 |
| default pool `e2-standard-4` | 1 | $0.02181/vCPU-hr + $0.00292/GiB-hr | 98 |
| voice pool `c2-standard-8` | 3 | $0.03398/vCPU-hr + $0.00456/GiB-hr | 915 |
| Cloud SQL regional, 2 vCPU / 7.5 GiB | 1 | $0.0826/vCPU-hr + $0.014/GiB-hr | 197 |
| Cloud SQL storage, regional SSD | 20 GB | $0.17/GB-mo ×2 | 7 |
| Memorystore Redis Basic M1 | 1 GiB | $0.049/GiB-hr | 36 |
| forwarding rules | 4 | $0.025/hr | 73 |
| static IPs | 4 | $0.010/hr | 29 |
| boot disks, egress | — | approximate | ~70 |
| **total** | | | **~1,500** |

The voice pool is 61% of it. Fixed cost is deliberately **not** folded into any
per-minute figure: it does not vary with a call, and dividing it by current
volume produces a per-minute number that falls as usage rises and looks like
efficiency when it is only division.

### `price_rules`

| column | type | note |
| --- | --- | --- |
| `scope` | str(64) | a `model_id`, or `*` for the global default |
| `explicit_per_minute_usd` | Numeric(12,6) \| null | final price; ignores cost |
| `markup_pct` | Numeric(6,3) \| null | e.g. `300.0` = cost × 4 |
| `fixed_per_minute_usd` | Numeric(12,6) \| null | added after markup |
| `effective_from_ms` | bigint | |
| `note` | str(255) | |

Evaluated in precedence order — explicit, then the model's own rule, then the
global default:

```sql
price_per_min = COALESCE(
  model_rule.explicit_per_minute_usd,
  -- only when the model rule has a usable knob; an all-NULL rule falls through
  cost_per_min * (1 + model_rule.markup_pct/100) + COALESCE(model_rule.fixed_per_minute_usd, 0),
  global_rule.explicit_per_minute_usd,
  cost_per_min * (1 + global_rule.markup_pct/100) + COALESCE(global_rule.fixed_per_minute_usd, 0),
  NULL  -- rule_source 'none'; never fall back to cost
)
```

Seeded with a single `*` rule. **Its value is a placeholder, not a
recommendation**: `markup_pct = 300` puts a Live minute at ~$0.054, below
Retell's $0.07 floor and about 4× cost. The rollout requires an operator to
confirm or change it before the customer-visible switch, because nobody but
you can decide what Arhiteq charges.

### `pricing_assumptions`

A four-row key/value table: `audio_tokens_per_sec` (25), `agent_talk_ratio`
(0.5), `turns_per_min` (4), `output_tokens_per_turn` (150).

These exist because the same constants are otherwise needed in two languages:
the SQL view computes a per-minute price, and `estimates.ts` computes a token
range for the editor. Private copies would let the picker and the estimate
disagree while both look right. The endpoint returns them, so TypeScript reads
what SQL used.

## The rule has exactly one implementation

`pricing.model_price` is a view over the three tables:

| column | meaning |
| --- | --- |
| `model_id` | |
| `cost_per_min` | audio models: `tokens_per_min/1e6 × in + talk_ratio × tokens_per_min/1e6 × out`; text models: the turn model, plus `cartesia_stt + cartesia_tts` |
| `price_per_min` | the COALESCE above |
| `rule_source` | `explicit` \| `model` \| `global` \| `none` — so a panel can show *why* |

The API selects from it and so do the Grafana margin panels. Implemented twice
— once in Python for the endpoint, once in SQL for the dashboard — the two
would drift, and the day they disagree the price list and the margin report
tell different stories about the same call.

`rule_source` is in the view because "why is this model priced at $0.09?" is a
question that gets asked, and reconstructing the answer from three tables by
hand is how wrong prices survive.

**When no usable rule resolves, `price_per_min` is NULL and `rule_source` is
`none` — never a price equal to cost.** The first implementation coalesced a
missing markup to zero, which priced every model at exactly cost: a 4x
under-charge that raised no error and looked like a working price list. A
missing component rate nulls `cost_per_min_stack` the same way rather than
costing zero. One rule underneath both: a missing input reads as *unknown*,
never as *free*, because a silent zero looks like a complete number.

Consumers must therefore render NULL as "unpriced", never as `$0.00` — the
endpoint below fails loudly instead of serving one, and the dashboard's NULL
rule already covers the panels.

## Endpoint

`GET /dashboard/pricing/models`, behind the existing dashboard session auth.

```json
{
  "models": [
    { "model_id": "gemini-live-2.5-flash-native-audio", "is_audio": true,
      "per_minute": 0.054 }
  ],
  "unpriced": ["gemini-3.1-flash-live-preview"],
  "components": { "cartesia_stt": 0.0088, "cartesia_tts": 0.056 }
}
```

**Every number in that payload is a price. No cost field is ever serialised.**
The response shape has no place to put one, which is the point: a leak has to
be a deliberate schema change, not an accidental field. `assumptions`,
`input_per_1m`, `output_per_1m` and `per_minute_adder` were in an earlier draft
of this shape and were deliberately removed before ship: the provider's own
per-token rates are public, so serving our marked-up per-token rates alongside
the assumptions that turn them into a per-minute figure lets anyone divide the
two out and reconstruct our exact cost and markup. `per_minute` is computed
and marked up entirely server-side, and it is the only number a consumer gets.

A model whose price is unknown — no rule resolves it, or the resolved price
does not clear the model's own cost — is omitted from `models` and named in
`unpriced` instead, so the frontend falls back to its compiled-in price rather
than rendering `$0.00`. `components` (the shared STT/TTS legs, priced at the
multiplier of whichever model resolves through the global rule) is present
only when such a model exists.

## Frontend

`estimates.ts` stops owning prices. `LLM_RATES` and the Cartesia/infra
constants are replaced by the fetched payload, threaded in from a hook that
loads the price list once per editor session.

**The compiled-in table stays as a fallback**, keeping its `satisfies
Record<LlmModelId, LlmRate>` check — so adding a model to the catalog without a
price is still a compile error, and a failed fetch still renders an estimate.
The fallback holds last-known *prices*, not costs, so a stale render is
conservative rather than a giveaway. Showing a zero, an error, or a cost figure
to a customer are all worse outcomes than showing a slightly stale price.

The fallback carries the date it was refreshed, and a test asserts it is not
more than one markup-rule change behind — see below.

## Verification

**View.** Precedence: an explicit price wins over a model rule; a model rule
wins over the global; a model with no rule at all falls back to global. Effect
dating: a call-time lookup returns the rule in force then, not the newest.
Audio vs text: a Live model prices off audio tokens and never enters the turn
model; a text model includes STT and TTS and never applies the audio path.

**Endpoint.** Asserts no cost value appears anywhere in the response, by
comparing every numeric leaf against the seeded costs. This is the regression
guard that matters: the failure mode is silent and it is a business leak, not
a crash.

**Frontend.** The fallback path renders a real estimate when the fetch fails.
A test asserts every fallback price is ≥ its corresponding cost, so a stale
fallback can never quote below cost.

## Rollout

1. Merge the backend: tables, seeds, view, endpoint. Nothing customer-visible
   changes — `estimates.ts` still uses its constants.
2. **Operator: set the global markup.** The seeded 300% is a placeholder. This
   is the one step nobody else can take.
3. Verify prices by querying `pricing.model_price` — check `rule_source` and
   `price_per_min` per model before anyone sees them.
4. Merge the frontend switch. The picker now quotes prices.
5. Refresh the compiled-in fallback to match the live prices.

Steps 2 and 3 sit between the two merges on purpose: the customer-visible
change lands only after the numbers have been looked at.

## Where this goes next

**Per-workspace pricing.** A `workspace_price_override` table joined ahead of
the global rule, when a real customer negotiates a real rate.

**Metering** replaces the assumed talk ratio and turn counts with measurements,
changing `cost_per_min` inside the view and nothing else.

**Margin reporting** is the metrics dashboard spec, which reads
`pricing.model_price` for the price side of every call.
