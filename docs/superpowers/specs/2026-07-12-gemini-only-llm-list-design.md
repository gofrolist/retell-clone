# Gemini-only LLM model list — design

**Date:** 2026-07-12
**Status:** Approved

## Problem

The agent editor's LLM dropdown offers GPT-4o, GPT-4o mini, and Claude 3.7
Sonnet alongside two Gemini models, but the deployment only provisions Google
API keys and the worker runs conversation exclusively on Gemini — any
non-Gemini model id silently falls back to the default Gemini model
(`worker/src/arhiteq_worker/main.py`, `_gemini_model()`). Offering models
that can never run is misleading. The list should be Gemini-only for now,
structured so more providers/models can be added later without rework.

There is no existing usage of non-Google model ids in any environment, so no
migration is needed.

## Design

### New: `frontend/src/lib/models.ts` (the extension point)

A single typed registry module owning the selectable model catalog:

```ts
export type LlmProvider = "google"; // widen later: | "openai" | "anthropic"

export interface LlmModel {
  id: string;       // wire value stored in the Retell LLM `model` field
  label: string;    // display name
  provider: LlmProvider;
}

export const LLM_MODELS: LlmModel[] = [
  { id: "gemini-3.5-flash",      label: "Gemini 3.5 Flash",      provider: "google" },
  { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite", provider: "google" },
  { id: "gemini-2.5-pro",        label: "Gemini 2.5 Pro",        provider: "google" },
  { id: "gemini-2.5-flash",      label: "Gemini 2.5 Flash",      provider: "google" },
  { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", provider: "google" },
];

export const POST_CALL_ANALYSIS_MODELS: LlmModel[] = [
  /* gemini-2.5-flash, gemini-2.5-flash-lite */
];
```

Model set: the five stable (non-preview) conversational models from
https://ai.google.dev/gemini-api/docs/models as of 2026-07-12. Preview models
are excluded deliberately.

Adding a provider later means: add entries here with a new `provider` value,
add provider dispatch + API key handling in the worker (`_gemini_model()` is
the current fallback point), and optionally group/price metadata for a richer
Retell-style picker.

### Consumers updated

- `frontend/src/components/editor/SelectorRow.tsx` — drop the local
  `MODEL_LABELS` map (which contains GPT/Claude entries); render options from
  `LLM_MODELS`. Keep the existing safety net that prepends the stored model id
  to the options when it is not in the registry, so an agent with an unknown
  model id still renders and stays selectable.
- `frontend/src/components/editor/sections/PostCallSection.tsx` — drop the
  local `MODEL_OPTIONS` (which contains `gpt-4o-mini`); render from
  `POST_CALL_ANALYSIS_MODELS`.
- `frontend/src/lib/mock.ts` — replace cosmetic non-Gemini display strings
  (e.g. `"GPT-5 mini"`) with Gemini labels for consistency.

### Deliberately unchanged

- **Backend** — the Retell LLM `model` column/schema stays a free-form string
  with no allow-list. The Retell wire contract accepts arbitrary model values;
  we never reject a value Retell would accept (prime directive).
- **Worker** — `_gemini_model()` keeps mapping any non-Gemini id to
  `ARHITEQ_GEMINI_MODEL` (default `gemini-2.5-flash`). No behavior change.
- **`MetaRow.tsx` cost/latency** — stays a hardcoded estimate; per-model
  pricing belongs to a future richer picker.
- **Post-call analysis runtime** — analysis actually uses the global
  `settings.analysis_model` config, not the per-agent field; the dropdown
  remains cosmetic and Retell-compatible.

## Error handling

No new failure modes: the registry is static frontend data. Unknown stored
model ids degrade exactly as today (rendered via the prepend safety net in the
dropdown; run on the default Gemini model in the worker).

## Testing

- `cd frontend && bun run build` and eslint (via pre-commit) must pass.
- Backend/worker contract tests are unaffected — none pin an allowed-model
  set (verified: `backend/tests/contract/test_crud_resources.py` uses
  `gemini-2.5-flash`; the `gpt-4.1` in `test_conversation_flow.py` is the
  separate conversation-flow `model_choice` field).
- Manual check: agent editor dropdown shows exactly the five Gemini models;
  post-call section shows the two flash models; saving a model round-trips
  through `update-retell-llm`.
