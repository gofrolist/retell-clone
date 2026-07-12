# Gemini-only LLM Model List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restrict the dashboard's selectable LLM models to Google Gemini only, sourced from a single typed registry module that is the future extension point for more providers.

**Architecture:** A new frontend-only registry module `frontend/src/lib/models.ts` owns the model catalog. The two dropdown components (`SelectorRow.tsx`, `PostCallSection.tsx`) render from it instead of local constants. Backend and worker are deliberately untouched: the backend stores `model` as a free-form Retell-compatible string, and the worker already maps any non-Gemini id to its default Gemini model.

**Tech Stack:** Next.js 16 / React 19 / TypeScript, bun as package manager. No frontend test framework exists — verification is `bun run build` and `bun run lint`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-12-gemini-only-llm-list-design.md`
- Do NOT modify backend or worker code — the Retell wire contract keeps `model` free-form (repo prime directive: never reject values Retell would accept).
- Model ids must be exactly: `gemini-3.5-flash`, `gemini-3.1-flash-lite`, `gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite` (stable models from ai.google.dev/gemini-api/docs/models as of 2026-07-12; no preview models).
- Preserve each dropdown's existing "prepend unknown stored model" safety net exactly as it behaves today.
- All commands run from `frontend/`: `bun run build`, `bun run lint`.
- Work on branch `feat/gemini-only-llm-list` (already exists, spec committed).
- This repo's frontend has a note (`frontend/AGENTS.md`) that its Next.js version has breaking changes vs training data — this plan touches no Next.js APIs, only plain TS modules and existing component internals, so no docs reading is required.

---

### Task 1: Model registry module

**Files:**
- Create: `frontend/src/lib/models.ts`

**Interfaces:**
- Consumes: nothing.
- Produces: `LlmProvider` (type), `LlmModel` (interface: `{ id: string; label: string; provider: LlmProvider }`), `LLM_MODELS: LlmModel[]` (5 entries), `POST_CALL_ANALYSIS_MODELS: LlmModel[]` (2 entries). Tasks 2 and 3 import these via `@/lib/models`.

- [ ] **Step 1: Create the registry file**

Create `frontend/src/lib/models.ts` with exactly:

```ts
// Selectable LLM catalog for the dashboard. This is the single extension
// point for adding models/providers later (widen LlmProvider, add entries,
// and add provider dispatch in the worker). The backend stores the id as a
// free-form Retell-compatible string; the worker runs conversation on
// Gemini only and maps unknown ids to its default Gemini model.
export type LlmProvider = "google";

export interface LlmModel {
  id: string; // wire value stored in the Retell LLM `model` field
  label: string;
  provider: LlmProvider;
}

// Stable (non-preview) conversational Gemini models,
// per https://ai.google.dev/gemini-api/docs/models (2026-07-12).
export const LLM_MODELS: LlmModel[] = [
  { id: "gemini-3.5-flash", label: "Gemini 3.5 Flash", provider: "google" },
  { id: "gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite", provider: "google" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro", provider: "google" },
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "google" },
  { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", provider: "google" },
];

// Post-call analysis dropdown (cosmetic today: analysis runs on the
// backend-configured global model). Cheap/fast models only.
export const POST_CALL_ANALYSIS_MODELS: LlmModel[] = [
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash", provider: "google" },
  { id: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite", provider: "google" },
];
```

- [ ] **Step 2: Verify build and lint pass**

Run: `cd frontend && bun run build && bun run lint`
Expected: build completes with no type errors; eslint reports no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/models.ts
git commit -m "feat(frontend): add LLM model registry module"
```

---

### Task 2: SelectorRow renders from the registry

**Files:**
- Modify: `frontend/src/components/editor/SelectorRow.tsx:8-16` (delete `MODEL_LABELS`) and `:43-45` (options derivation)

**Interfaces:**
- Consumes: `LLM_MODELS` from `@/lib/models` (Task 1).
- Produces: no exports change; `SelectorRow` props are untouched.

- [ ] **Step 1: Replace the local model map with the registry**

In `frontend/src/components/editor/SelectorRow.tsx`, delete lines 8–16:

```ts
// Retell-compatible engine model ids the backend stores as-is (the worker
// maps non-Gemini names to its default Gemini model).
const MODEL_LABELS: Record<string, string> = {
  "gemini-2.5-flash": "Gemini 2.5 Flash",
  "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
  "gpt-4o": "GPT-4o",
  "gpt-4o-mini": "GPT-4o mini",
  "claude-3.7-sonnet": "Claude 3.7 Sonnet",
};
```

and add this import after the existing imports (line 6):

```ts
import { LLM_MODELS } from "@/lib/models";
```

Then replace the options derivation (currently lines 43–45):

```ts
  const modelIds = Object.keys(MODEL_LABELS);
  if (model && !modelIds.includes(model)) modelIds.unshift(model);
  const modelOptions = modelIds.map((m) => ({ value: m, label: MODEL_LABELS[m] ?? m }));
```

with:

```ts
  const knownModels = LLM_MODELS.map((m) => ({ value: m.id, label: m.label }));
  const modelOptions =
    model && !LLM_MODELS.some((m) => m.id === model)
      ? [{ value: model, label: model }, ...knownModels]
      : knownModels;
```

(Same behavior as before: an unknown non-empty stored model id is prepended,
shown with its raw id as the label.)

- [ ] **Step 2: Verify build and lint pass**

Run: `cd frontend && bun run build && bun run lint`
Expected: success, no unused-variable warnings (MODEL_LABELS fully removed).

- [ ] **Step 3: Verify the dropdown manually (optional but recommended)**

Run: `cd frontend && bun run dev`, open an agent editor page, confirm the
model dropdown lists exactly: Gemini 3.5 Flash, Gemini 3.1 Flash Lite,
Gemini 2.5 Pro, Gemini 2.5 Flash, Gemini 2.5 Flash Lite — and no GPT/Claude
entries.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/editor/SelectorRow.tsx
git commit -m "feat(frontend): agent LLM dropdown offers Gemini models only"
```

---

### Task 3: PostCallSection renders from the registry

**Files:**
- Modify: `frontend/src/components/editor/sections/PostCallSection.tsx:15-19` (delete `MODEL_OPTIONS`) and `:28-30` (options derivation)

**Interfaces:**
- Consumes: `POST_CALL_ANALYSIS_MODELS` from `@/lib/models` (Task 1).
- Produces: no exports change; `PostCallSection` props are untouched.

- [ ] **Step 1: Replace the local options list with the registry**

In `frontend/src/components/editor/sections/PostCallSection.tsx`, delete
lines 15–19:

```ts
const MODEL_OPTIONS = [
  { value: "gemini-2.5-flash", label: "Gemini 2.5 Flash" },
  { value: "gemini-2.5-flash-lite", label: "Gemini 2.5 Flash Lite" },
  { value: "gpt-4o-mini", label: "GPT-4o mini" },
];
```

and add this import after the existing imports (line 5):

```ts
import { POST_CALL_ANALYSIS_MODELS } from "@/lib/models";
```

Then replace the options derivation inside the component (currently lines
28–30):

```ts
  const options = MODEL_OPTIONS.some((o) => o.value === model)
    ? MODEL_OPTIONS
    : [{ value: model, label: model }, ...MODEL_OPTIONS];
```

with:

```ts
  const knownModels = POST_CALL_ANALYSIS_MODELS.map((m) => ({ value: m.id, label: m.label }));
  const options = POST_CALL_ANALYSIS_MODELS.some((m) => m.id === model)
    ? knownModels
    : [{ value: model, label: model }, ...knownModels];
```

(Preserves this component's existing behavior exactly, including prepending
even when `model` is an empty string.)

- [ ] **Step 2: Verify build and lint pass**

Run: `cd frontend && bun run build && bun run lint`
Expected: success.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/editor/sections/PostCallSection.tsx
git commit -m "feat(frontend): post-call extraction model dropdown is Gemini-only"
```

---

### Task 4: Mock data cleanup

**Files:**
- Modify: `frontend/src/lib/mock.ts:127,174,189,204` (the four `llm_model: "GPT-5 mini"` occurrences)

**Interfaces:**
- Consumes: nothing from earlier tasks (display-only mock strings).
- Produces: nothing.

- [ ] **Step 1: Replace non-Gemini mock display strings**

In `frontend/src/lib/mock.ts`, replace every occurrence of:

```ts
    llm_model: "GPT-5 mini",
```

with:

```ts
    llm_model: "Gemini 2.5 Flash Lite",
```

There are exactly 4 occurrences (lines 127, 174, 189, 204). The existing
`llm_model: "Gemini 3.1 Flash Lite"` occurrences (lines 30, 91, 156) now
match a registry model and stay as-is, as does `model: "gemini-2.5-flash"`
(line 516).

- [ ] **Step 2: Verify no non-Google model strings remain in the frontend**

Run: `cd frontend && grep -rn -i "gpt\|claude" src/`
Expected: no matches (if anything appears, it must not be a selectable-model
or mock `llm_model` string — investigate before proceeding).

- [ ] **Step 3: Verify build and lint pass**

Run: `cd frontend && bun run build && bun run lint`
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/mock.ts
git commit -m "chore(frontend): align mock llm_model strings with Gemini-only catalog"
```
