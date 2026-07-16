# Agent Details estimates — design

**Date:** 2026-07-14
**Status:** approved design, pre-implementation

## Problem

The agent editor header (`frontend/src/components/editor/MetaRow.tsx`) shows
hardcoded placeholders: `Cost $0.120/min` and `Latency 800-1200ms` with a
`title="Estimate"` tooltip. Retell's equivalent header shows three live,
per-component breakdowns (Cost, Latency, Tokens) that update as the agent is
edited. Ours should too.

## Decisions (from brainstorming)

- **Scope:** all three chips — Cost, Latency, Tokens — each with a popover
  breakdown, Retell-style.
- **Cost basis:** real provider costs for our actual runtime stack
  (Gemini LLM + Cartesia STT/TTS + LiveKit), not Retell's price list.
- **Architecture:** frontend-only. Pure functions + a rate card of constants
  in `frontend/src/lib/estimates.ts`. No backend changes, no new endpoints,
  no wire-contract risk.

## Runtime facts the estimates model

- Worker pipeline is **Cartesia STT (ink-whisper) → Gemini → Cartesia TTS
  (sonic-2)** (`worker/src/arhiteq_worker/main.py`); all voice ids resolve
  to Cartesia voices regardless of UI prefix.
- The system prompt sent to the LLM is the agent's `general_prompt` with
  variables resolved, plus at most a ~40-token backchannel suffix — there is
  no large fixed platform overhead, so no "Agent Handbook"-style token row.
- The editor page (`frontend/src/app/agents/[id]/page.tsx`) holds draft state
  (`llmView`, `agentView` equivalents) and already has in scope everything
  the estimator needs: `llm.model`, `general_prompt`, `begin_message`,
  `general_tools`, `knowledge_base_ids`, `agent.voice_id`, `language`.

## Component 1 — `frontend/src/lib/estimates.ts`

Pure, dependency-free functions plus a rate card of named constants. Every
constant carries a comment with source URL and "as of 2026-07-14".

### Token estimation

`estimateTokens(llm: RawLlm | null): TokenEstimate`

Heuristic: `tokens ≈ ceil(chars / 4)`.

| Row | Min | Max |
|---|---|---|
| System Prompt | chars(`general_prompt` + `begin_message`) / 4 | same + 10% |
| Tool Definitions | chars(JSON.stringify(`general_tools`)) / 4 | same |
| Conversation History | 250 | 2,200 |
| Knowledge Base (only if `knowledge_base_ids.length > 0`) | 200 | 1,500 |

Total = sum of rows. Returns per-row ranges plus the total range so the UI
can render the breakdown directly. If `llm` is null, returns null and the
Tokens chip is hidden.

### Cost estimation ($/min)

`estimateCost(llm, tokens): CostEstimate` — needs only the LLM view; the
voice stack is fixed (all voices resolve to Cartesia).

Turn model constants: `TURNS_PER_MIN = 4`, `OUTPUT_TOKENS_PER_TURN = 150`.

| Row | Formula / value |
|---|---|
| LLM: {model} | `TURNS_PER_MIN × (maxTokens / 1e6 × inputPrice + OUTPUT_TOKENS_PER_TURN / 1e6 × outputPrice)` |
| STT: cartesia ink-whisper | $0.0024/min flat |
| TTS: cartesia sonic-2 | $0.014/min (≈$0.028/min of speech × 50% agent talk-time) |
| Voice Infra (LiveKit) | $0.001/min (2 participant-connections × $0.0005) |
| Knowledge Base (only if attached) | $0.001/min |

Total = sum, displayed as `$0.0XX/min` (3 decimals). The LLM row scales with
the live token estimate, so growing the prompt visibly raises the price —
the honest analogue of Retell's 4k-token surcharge. There is **no threshold
surcharge section**: we pay providers linearly.

LLM rate card ($ per 1M tokens, Google list prices, standard context,
paid tier, as of 2026-07-14 — source https://ai.google.dev/gemini-api/docs/pricing):

| Model | Input | Output |
|---|---|---|
| gemini-3.5-flash | 1.50 | 9.00 |
| gemini-3.1-flash-lite | 0.25 | 1.50 |
| gemini-2.5-pro | 1.25 | 10.00 |
| gemini-2.5-flash | 0.30 | 2.50 |
| gemini-2.5-flash-lite | 0.10 | 0.40 |

Caveat noted in code: Gemini 3.x output prices include thinking tokens, so
real output cost per visible token can be higher; the estimate stays a
"list-price floor". Unknown model id → fall back to `DEFAULT_LLM_RATE`
(gemini-2.5-flash values) so the UI never breaks on catalog drift.

### Latency estimation (ms range)

`estimateLatency(llm, hasKb): LatencyEstimate` — total = sum of row mins /
sum of row maxes.

| Row | Range (ms) | Basis |
|---|---|---|
| Transcription (cartesia ink-whisper) | 60–100 | Cartesia published median 66 / P90 98 |
| LLM: {model} TTFT | per-model, see below | our estimate; Google publishes none |
| TTS (cartesia sonic-2) | 90–200 | published sub-90ms model TTFB + network |
| Knowledge Base (only if attached) | 75–125 | retrieval round-trip estimate |

Per-model TTFT estimates (documented as estimates, not published figures):
flash-lite tiers 250–450; 2.5/3.5 flash 350–600; 2.5-pro 600–1200.

## Component 2 — UI (`MetaRow.tsx` + new `HoverCard`)

- `MetaRow` props change `{agentId}` → `{agentId, llm}` where `llm`
  is the **draft** `llmView` from the page, so estimates recompute on every
  keystroke/model switch. Call site: `frontend/src/app/agents/[id]/page.tsx`.
- Renders three chips with dotted underlines: `Cost $X.XXX/min`,
  `Latency X–Yms`, `Tokens X.Xk–Y.Yk` (k-formatting ≥1,000).
- Each chip opens a **`HoverCard`** — new small component in
  `frontend/src/components/ui/`, same visual language as the existing
  `Tooltip.tsx` but sized for content rows: headline (label + big value),
  divider, component rows (`name … value`), optional footnote line. Opens on
  hover/focus, closes on leave/blur/Escape; keyboard-accessible
  (`tabIndex=0`, `aria-describedby`).
- Tokens popover extra: when max tokens > 14,000, show the warning line
  "Prompts exceeding 14,000 tokens significantly increase hallucination
  risk." (mirrors Retell's useful hint; threshold constant in estimates.ts).
- Null handling: `llm === null` (non-retell-llm engine) → Tokens chip
  hidden; Cost/Latency still render from the voice-stack rows with no LLM
  row.

## Error handling

- Unknown LLM model → `DEFAULT_LLM_RATE` fallback (never throw, never NaN).
- Missing/empty prompt or tools → those contribute 0 tokens; UI still renders.
- All functions are total: any malformed input degrades to omitting a row,
  never to a crashed header.

## Testing & verification

- No frontend test runner exists (build + eslint only) and adding one is out
  of scope. `estimates.ts` is pure and deterministic to keep it trivially
  unit-testable later.
- Verification: `bun run build`, eslint via pre-commit, then drive the
  dashboard against a real agent and sanity-check the numbers by hand
  against the rate-card table above.

## Out of scope

- Backend/API changes of any kind (wire contract untouched).
- Billing reconciliation against actual post-call `call_cost` records.
- Operator-configurable rate card (revisit if provider prices churn).
- Populating the unused aspirational fields on the dashboard `Agent` type
  (`cost_per_min`, `latency_ms`, `token_range` in `frontend/src/lib/types.ts`).
