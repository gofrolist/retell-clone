# Agent Details Estimates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Historical note:** executed 2026-07-14. Code blocks below are the plan-time versions; review fix waves superseded some details — STT_COST_PER_MIN is 0.0022 (not 0.0024, commit efb6e9c), HoverCard bridges the trigger→panel gap with inner padding (commit 4d9e9e2), and HoverCard wires aria-describedby. The shipped code in frontend/src is authoritative.

**Goal:** Replace the hardcoded `Cost $0.120/min · Latency 800-1200ms` placeholders in the agent editor header with live, per-component Cost / Latency / Tokens estimate popovers (Retell-style), computed from the agent's draft config.

**Architecture:** Frontend-only. A pure, dependency-free estimation module (`frontend/src/lib/estimates.ts`) holds a rate card of provider constants (Gemini, Cartesia, LiveKit — sourced, dated) and three total functions. A new pure-CSS `HoverCard` UI primitive (modeled on the existing `Tooltip.tsx`) renders breakdowns. `MetaRow.tsx` computes estimates from the page's draft `llmView` on every render, so numbers update live while typing.

**Tech Stack:** Next.js 16.2.10 (App Router, client components), React 19, Tailwind CSS v4, TypeScript 6, bun.

**Spec:** `docs/superpowers/specs/2026-07-14-agent-details-estimates-design.md`

## Global Constraints

- Work on branch `feat/agent-details-estimates` (already exists, holds the spec commit). `main` is PR-only; PR title must be a conventional commit.
- Package manager is **bun**; run all frontend commands from `frontend/`.
- **No new dependencies.** No backend/API/wire-contract changes of any kind.
- `@/*` resolves to `frontend/src/*`.
- This Next.js version has breaking changes vs. training data — if any Next-specific behavior surprises you, check `frontend/node_modules/next/dist/docs/` before working around it. (The code below is plain client components; nothing Next-specific should come up.)
- The frontend has **no test runner** (build + eslint only). Task 1 uses a temporary check script executed with `bun`, deleted before commit. Do not add a test framework.
- All rate-card constants are Google/Cartesia/LiveKit list prices "as of 2026-07-14" with source URLs in comments — keep those comments.
- pre-commit hooks (gitleaks, ruff, pytest, eslint) run on `git commit`. Do not use `--no-verify`.

---

### Task 1: Estimation module `estimates.ts`

**Files:**
- Create: `frontend/src/lib/estimates.ts`
- Test (temporary, deleted in step 5): `frontend/estimates.check.ts`

**Interfaces:**
- Consumes: `RawLlm` type from `frontend/src/lib/api.ts:157-208` (type-only import; fields used: `model`, `general_prompt`, `begin_message`, `general_tools`, `knowledge_base_ids`).
- Produces (used by Task 3):
  - `interface EstimateRow { label: string; min: number; max: number }`
  - `interface Estimate { rows: EstimateRow[]; min: number; max: number }`
  - `estimateTokens(llm: RawLlm | null): Estimate | null`
  - `estimateCost(llm: RawLlm | null, tokens: Estimate | null): Estimate`
  - `estimateLatency(llm: RawLlm | null): Estimate`
  - `formatUsdPerMin(v: number): string` → `"$0.026/min"`
  - `formatTokenValue(n: number): string` → `"16.1k"` / `"680"`
  - `TOKEN_WARNING_THRESHOLD` (number, 14000)

- [ ] **Step 1: Write the failing check script**

Create `frontend/estimates.check.ts`:

```ts
// Temporary verification script (no test runner in this app). Run with:
//   bun estimates.check.ts
// Deleted before commit.
import assert from "node:assert/strict";
import {
  estimateCost,
  estimateLatency,
  estimateTokens,
  formatTokenValue,
  formatUsdPerMin,
  TOKEN_WARNING_THRESHOLD,
} from "./src/lib/estimates";
import type { RawLlm } from "./src/lib/api";

const approx = (a: number, b: number) => Math.abs(a - b) < 1e-9;

const llm: RawLlm = {
  llm_id: "llm_check",
  model: "gemini-2.5-flash",
  model_temperature: 0,
  general_prompt: "x".repeat(4000), // 1000 tokens at 4 chars/token
  begin_message: null,
  start_speaker: "agent",
  general_tools: [{ name: "end_call", type: "end_call" }],
  knowledge_base_ids: ["kb_1"],
  last_modification_timestamp: 0,
};

// --- tokens ---
const tokens = estimateTokens(llm);
assert.ok(tokens, "tokens estimate exists when llm present");
assert.equal(tokens.rows.length, 4, "prompt + tools + history + kb rows");
const [prompt, tools, history, kb] = tokens.rows;
assert.equal(prompt.label, "System Prompt");
assert.equal(prompt.min, 1000);
assert.equal(prompt.max, 1100); // +10%
assert.equal(tools.label, "Tool Definitions");
assert.equal(tools.min, Math.ceil(JSON.stringify(llm.general_tools).length / 4));
assert.equal(history.label, "Conversation History");
assert.deepEqual([history.min, history.max], [250, 2200]);
assert.equal(kb.label, "Knowledge Base");
assert.deepEqual([kb.min, kb.max], [200, 1500]);
assert.equal(tokens.min, prompt.min + tools.min + 250 + 200);
assert.equal(tokens.max, prompt.max + tools.max + 2200 + 1500);

// --- cost ---
const cost = estimateCost(llm, tokens);
assert.equal(cost.rows.length, 5, "llm + stt + tts + infra + kb");
const llmRow = cost.rows[0];
assert.equal(llmRow.label, "LLM: gemini-2.5-flash");
// 4 turns/min x (maxTokens input @ $0.30/1M + 150 output tokens @ $2.50/1M)
const expectedLlm = 4 * ((tokens.max / 1e6) * 0.3 + (150 / 1e6) * 2.5);
assert.ok(approx(llmRow.max, expectedLlm), `llm cost ${llmRow.max} != ${expectedLlm}`);
assert.ok(approx(cost.max, expectedLlm + 0.0024 + 0.014 + 0.001 + 0.001));

// unknown model falls back to the default rate instead of crashing
const unknown = estimateCost({ ...llm, model: "gemini-99-ultra" }, tokens);
assert.ok(unknown.max > 0 && Number.isFinite(unknown.max));

// --- latency (gemini-2.5-flash TTFT 350-600, stt 60-100, tts 90-200, kb 75-125) ---
const latency = estimateLatency(llm);
assert.equal(latency.rows.length, 4);
assert.equal(latency.min, 60 + 350 + 90 + 75);
assert.equal(latency.max, 100 + 600 + 200 + 125);

// --- null llm (conversation-flow agents) ---
assert.equal(estimateTokens(null), null);
const nullCost = estimateCost(null, null);
assert.equal(nullCost.rows.length, 3, "stt + tts + infra only");
const nullLatency = estimateLatency(null);
assert.deepEqual([nullLatency.min, nullLatency.max], [150, 300]);

// --- formatters ---
assert.equal(formatUsdPerMin(0.0256), "$0.026/min");
assert.equal(formatTokenValue(16123), "16.1k");
assert.equal(formatTokenValue(680), "680");
assert.equal(TOKEN_WARNING_THRESHOLD, 14000);

console.log("estimates.check.ts: all assertions passed");
```

- [ ] **Step 2: Run the check to verify it fails**

Run: `cd frontend && bun estimates.check.ts`
Expected: FAIL — `Cannot find module './src/lib/estimates'` (or equivalent resolution error).

- [ ] **Step 3: Implement `estimates.ts`**

Create `frontend/src/lib/estimates.ts`:

```ts
// Pre-call cost / latency / token estimates for the agent editor header.
// Pure functions over the draft agent config — no fetches, no side effects.
//
// The rate card models our actual runtime pipeline (see worker/src/
// arhiteq_worker/main.py): Cartesia STT (ink-whisper) -> Gemini LLM ->
// Cartesia TTS (sonic-2), on LiveKit. All voice ids resolve to Cartesia
// voices, so STT/TTS rates don't depend on the selected voice.
//
// Every constant is a provider list price or published figure as of
// 2026-07-14; update in place when providers reprice.

import type { RawLlm } from "@/lib/api";

export interface EstimateRow {
  label: string;
  min: number;
  max: number;
}

export interface Estimate {
  rows: EstimateRow[];
  min: number;
  max: number;
}

interface LlmRate {
  inputPer1M: number; // USD per 1M input tokens
  outputPer1M: number; // USD per 1M output tokens
  ttftMs: [number, number]; // time-to-first-token; our estimate, Google publishes none
}

// Google list prices, paid tier, standard context, as of 2026-07-14:
// https://ai.google.dev/gemini-api/docs/pricing
// Gemini 3.x output prices include thinking tokens, so real output cost per
// visible token can run higher — treat these as a list-price floor.
const LLM_RATES: Record<string, LlmRate> = {
  "gemini-3.5-flash": { inputPer1M: 1.5, outputPer1M: 9.0, ttftMs: [350, 600] },
  "gemini-3.1-flash-lite": { inputPer1M: 0.25, outputPer1M: 1.5, ttftMs: [250, 450] },
  "gemini-2.5-pro": { inputPer1M: 1.25, outputPer1M: 10.0, ttftMs: [600, 1200] },
  "gemini-2.5-flash": { inputPer1M: 0.3, outputPer1M: 2.5, ttftMs: [350, 600] },
  "gemini-2.5-flash-lite": { inputPer1M: 0.1, outputPer1M: 0.4, ttftMs: [250, 450] },
};

// Catalog drift safety net: unknown model ids estimate as gemini-2.5-flash.
const DEFAULT_LLM_RATE: LlmRate = LLM_RATES["gemini-2.5-flash"];

const CHARS_PER_TOKEN = 4; // standard rough heuristic for English text
const TURNS_PER_MIN = 4; // assumed LLM requests per call minute
const OUTPUT_TOKENS_PER_TURN = 150; // assumed visible tokens per response
const HISTORY_TOKENS: [number, number] = [250, 2200]; // grows over the call
const KB_TOKENS: [number, number] = [200, 1500]; // retrieved chunks per turn

// Cartesia (https://docs.cartesia.ai/pricing, Scale tier, 2026-07-14):
// STT ink-whisper 1 credit/sec realtime at $37.375/M credits ~= $0.0024/min.
// TTS ~1 credit/char, ~750 chars/min of speech ~= $0.028/min of speech;
// agent speaks ~50% of a call minute -> $0.014 per call minute.
const STT_COST_PER_MIN = 0.0024;
const TTS_COST_PER_MIN = 0.014;
// LiveKit Cloud (https://livekit.com/pricing, 2026-07-14): $0.0005 per
// participant-minute overage x 2 connections (caller + worker).
const INFRA_COST_PER_MIN = 0.001;
// Embedding/retrieval overhead when a knowledge base is attached; rounded.
const KB_COST_PER_MIN = 0.001;

// Latency figures (ms). STT: Cartesia's published ink-whisper streaming
// benchmark (median 66 / P90 98). TTS: published sub-90ms sonic-2 model TTFB
// plus network. KB: retrieval round-trip estimate.
const STT_LATENCY_MS: [number, number] = [60, 100];
const TTS_LATENCY_MS: [number, number] = [90, 200];
const KB_LATENCY_MS: [number, number] = [75, 125];

// Mirrors Retell's editor hint threshold.
export const TOKEN_WARNING_THRESHOLD = 14000;

const hasKb = (llm: RawLlm | null): boolean =>
  (llm?.knowledge_base_ids ?? []).length > 0;

const total = (rows: EstimateRow[]): Estimate => ({
  rows,
  min: rows.reduce((s, r) => s + r.min, 0),
  max: rows.reduce((s, r) => s + r.max, 0),
});

/** Per-turn prompt size. Null for conversation-flow agents (no retell-llm). */
export function estimateTokens(llm: RawLlm | null): Estimate | null {
  if (!llm) return null;
  const promptChars =
    (llm.general_prompt ?? "").length + (llm.begin_message ?? "").length;
  const promptTokens = Math.ceil(promptChars / CHARS_PER_TOKEN);
  const rows: EstimateRow[] = [
    // +10% headroom: resolved {{variables}} usually expand the raw template
    { label: "System Prompt", min: promptTokens, max: Math.ceil(promptTokens * 1.1) },
  ];
  const tools = llm.general_tools ?? [];
  if (tools.length > 0) {
    const toolTokens = Math.ceil(JSON.stringify(tools).length / CHARS_PER_TOKEN);
    rows.push({ label: "Tool Definitions", min: toolTokens, max: toolTokens });
  }
  rows.push({
    label: "Conversation History",
    min: HISTORY_TOKENS[0],
    max: HISTORY_TOKENS[1],
  });
  if (hasKb(llm)) {
    rows.push({ label: "Knowledge Base", min: KB_TOKENS[0], max: KB_TOKENS[1] });
  }
  return total(rows);
}

/** USD per call minute. Cost rows are single values (min === max). */
export function estimateCost(
  llm: RawLlm | null,
  tokens: Estimate | null,
): Estimate {
  const rows: EstimateRow[] = [];
  if (llm && tokens) {
    const rate = LLM_RATES[llm.model] ?? DEFAULT_LLM_RATE;
    const perMin =
      TURNS_PER_MIN *
      ((tokens.max / 1e6) * rate.inputPer1M +
        (OUTPUT_TOKENS_PER_TURN / 1e6) * rate.outputPer1M);
    rows.push({ label: `LLM: ${llm.model}`, min: perMin, max: perMin });
  }
  rows.push({
    label: "STT: cartesia ink-whisper",
    min: STT_COST_PER_MIN,
    max: STT_COST_PER_MIN,
  });
  rows.push({
    label: "TTS: cartesia sonic-2",
    min: TTS_COST_PER_MIN,
    max: TTS_COST_PER_MIN,
  });
  rows.push({ label: "Voice Infra", min: INFRA_COST_PER_MIN, max: INFRA_COST_PER_MIN });
  if (hasKb(llm)) {
    rows.push({ label: "Knowledge Base", min: KB_COST_PER_MIN, max: KB_COST_PER_MIN });
  }
  return total(rows);
}

/** End-to-end turn latency range: sum of per-component ranges. */
export function estimateLatency(llm: RawLlm | null): Estimate {
  const rows: EstimateRow[] = [
    { label: "Transcription", min: STT_LATENCY_MS[0], max: STT_LATENCY_MS[1] },
  ];
  if (llm) {
    const rate = LLM_RATES[llm.model] ?? DEFAULT_LLM_RATE;
    rows.push({ label: `LLM: ${llm.model}`, min: rate.ttftMs[0], max: rate.ttftMs[1] });
  }
  rows.push({
    label: "TTS: cartesia sonic-2",
    min: TTS_LATENCY_MS[0],
    max: TTS_LATENCY_MS[1],
  });
  if (hasKb(llm)) {
    rows.push({ label: "Knowledge Base", min: KB_LATENCY_MS[0], max: KB_LATENCY_MS[1] });
  }
  return total(rows);
}

export function formatUsdPerMin(v: number): string {
  return `$${v.toFixed(3)}/min`;
}

export function formatTokenValue(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}
```

- [ ] **Step 4: Run the check to verify it passes**

Run: `cd frontend && bun estimates.check.ts`
Expected: `estimates.check.ts: all assertions passed`

- [ ] **Step 5: Delete the check script and commit**

```bash
rm frontend/estimates.check.ts
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
git add frontend/src/lib/estimates.ts
git commit -m "feat: add cost/latency/token estimation rate card"
```

---

### Task 2: `HoverCard` UI primitive

**Files:**
- Create: `frontend/src/components/ui/HoverCard.tsx`

**Interfaces:**
- Consumes: `cn` from `@/lib/utils` (same as `Tooltip.tsx`).
- Produces (used by Task 3): default export `HoverCard({ trigger, children, className }: { trigger: ReactNode; children: ReactNode; className?: string })` — renders `trigger` inline; shows a popover panel below it on hover/focus.

Known deviation from spec: the spec mentions Escape-to-close; the pure-CSS approach (matching `Tooltip.tsx`'s idiom) closes on mouse-leave and blur/Tab-away instead, which covers keyboard users without adding JS state. Do not add an Escape handler.

- [ ] **Step 1: Implement the component**

Create `frontend/src/components/ui/HoverCard.tsx`:

```tsx
"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * Pure-CSS hover popover, following Tooltip.tsx's approach: visibility is
 * driven by :hover / :focus-within on the wrapper, so it needs no JS state.
 * Unlike Tooltip it hosts arbitrary content rows, so the panel is opaque,
 * bordered, and interactive-width.
 */
export default function HoverCard({
  trigger,
  children,
  className,
}: {
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className="group/card relative inline-flex">
      <span
        tabIndex={0}
        className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent-deep/40"
      >
        {trigger}
      </span>
      <div
        role="tooltip"
        className={cn(
          "invisible absolute left-1/2 top-full z-30 mt-1.5 w-72 -translate-x-1/2 rounded-xl border border-line bg-card p-2 opacity-0 shadow-lg transition-opacity",
          "group-hover/card:visible group-hover/card:opacity-100 group-focus-within/card:visible group-focus-within/card:opacity-100",
          className,
        )}
      >
        {children}
      </div>
    </span>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && bunx tsc --noEmit`
Expected: exit 0, no errors. (This checks the whole app; `estimates.ts` from Task 1 is covered too.)

- [ ] **Step 3: Commit**

```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
git add frontend/src/components/ui/HoverCard.tsx
git commit -m "feat: add HoverCard popover primitive"
```

---

### Task 3: Wire `MetaRow` to live estimates

**Files:**
- Modify: `frontend/src/components/editor/MetaRow.tsx` (replace whole file)
- Modify: `frontend/src/app/agents/[id]/page.tsx:163` (call site)

**Interfaces:**
- Consumes: everything Task 1 exports; `HoverCard` from Task 2; `CopyId` from `@/components/ui/CopyId`; `RawLlm` from `@/lib/api`.
- Produces: `MetaRow({ agentId, llm }: { agentId: string; llm: RawLlm | null })` — the page passes the **draft** `llmView` (line 144 of page.tsx: `{ ...llm, ...llmDraft }`), so estimates recompute per keystroke.

- [ ] **Step 1: Replace `MetaRow.tsx`**

Replace the entire contents of `frontend/src/components/editor/MetaRow.tsx` with:

```tsx
"use client";

import CopyId from "@/components/ui/CopyId";
import HoverCard from "@/components/ui/HoverCard";
import type { RawLlm } from "@/lib/api";
import {
  type Estimate,
  estimateCost,
  estimateLatency,
  estimateTokens,
  formatTokenValue,
  formatUsdPerMin,
  TOKEN_WARNING_THRESHOLD,
} from "@/lib/estimates";

const msRange = (min: number, max: number) =>
  `${Math.round(min)}-${Math.round(max)}ms`;
const tokenRange = (min: number, max: number) =>
  min === max
    ? formatTokenValue(max)
    : `${formatTokenValue(min)} - ${formatTokenValue(max)}`;

function Headline({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-app px-3 py-2 text-left">
      <div className="text-[11px] text-sub">{label}</div>
      <div className="text-[15px] font-semibold text-ink">{value}</div>
    </div>
  );
}

function Rows({
  estimate,
  format,
}: {
  estimate: Estimate;
  format: (min: number, max: number) => string;
}) {
  return (
    <div className="mt-2 space-y-1.5 border-t border-dashed border-line px-1 pb-1 pt-2">
      {estimate.rows.map((row) => (
        <div
          key={row.label}
          className="flex items-center justify-between gap-4 text-[12px]"
        >
          <span className="text-sub">{row.label}</span>
          <span className="font-medium text-ink">{format(row.min, row.max)}</span>
        </div>
      ))}
    </div>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span>
      {label}{" "}
      <span className="cursor-default underline decoration-dotted underline-offset-2">
        {value}
      </span>
    </span>
  );
}

export default function MetaRow({
  agentId,
  llm,
}: {
  agentId: string;
  llm: RawLlm | null;
}) {
  const tokens = estimateTokens(llm);
  const cost = estimateCost(llm, tokens);
  const latency = estimateLatency(llm);

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-sub">
      <span className="font-medium text-ink">Agent Details</span>
      <HoverCard trigger={<Chip label="Cost" value={formatUsdPerMin(cost.max)} />}>
        <Headline label="Cost per minute" value={formatUsdPerMin(cost.max)} />
        <Rows estimate={cost} format={(_, max) => formatUsdPerMin(max)} />
      </HoverCard>
      <span aria-hidden>·</span>
      <HoverCard
        trigger={<Chip label="Latency" value={msRange(latency.min, latency.max)} />}
      >
        <Headline
          label="Estimated Latency"
          value={msRange(latency.min, latency.max)}
        />
        <Rows estimate={latency} format={msRange} />
      </HoverCard>
      {tokens ? (
        <>
          <span aria-hidden>·</span>
          <HoverCard
            trigger={<Chip label="Tokens" value={tokenRange(tokens.min, tokens.max)} />}
          >
            <Headline
              label="Estimated Tokens"
              value={`${tokens.min.toLocaleString("en-US")}–${tokens.max.toLocaleString("en-US")} tokens`}
            />
            {tokens.max > TOKEN_WARNING_THRESHOLD && (
              <p className="mt-2 px-1 text-[12px] font-medium text-amber-600">
                Prompts exceeding {TOKEN_WARNING_THRESHOLD.toLocaleString("en-US")}{" "}
                tokens significantly increase hallucination risk.
              </p>
            )}
            <Rows estimate={tokens} format={tokenRange} />
          </HoverCard>
        </>
      ) : null}
      <span className="ml-auto">
        <CopyId value={agentId} display="ID" />
      </span>
    </div>
  );
}
```

- [ ] **Step 2: Update the call site**

In `frontend/src/app/agents/[id]/page.tsx`, line 163, change:

```tsx
          <MetaRow agentId={agent.agent_id} />
```

to:

```tsx
          <MetaRow agentId={agent.agent_id} llm={llmView} />
```

(`llmView` is defined at line 144 as `llm ? { ...llm, ...llmDraft } : null` —
draft-overlaid, which is what makes the estimates live. It is declared after
the early returns but before the JSX, so it is in scope at line 163.)

- [ ] **Step 3: Build and lint**

Run: `cd frontend && bun run build && bun run lint`
Expected: build succeeds, eslint reports no errors.

- [ ] **Step 4: Commit**

```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
git add frontend/src/components/editor/MetaRow.tsx "frontend/src/app/agents/[id]/page.tsx"
git commit -m "feat: live cost/latency/token estimates in agent details header"
```

---

### Task 4: End-to-end verification + PR

**Files:** none created; verification and integration only.

**Interfaces:**
- Consumes: the running local stack (`docker compose up -d`, `make api`, `make web` from repo root) and everything from Tasks 1–3.

- [ ] **Step 1: Drive the real page**

Start the stack (repo root): `docker compose up -d && make api` (background) and `make web` (background). Open the dashboard, navigate to any agent's editor page.

Verify, comparing against the rate card in `frontend/src/lib/estimates.ts`:
1. Header shows three chips: `Cost $0.0XX/min`, `Latency XXX-XXXXms`, `Tokens X.Xk - X.Xk` — not the old `$0.120/min` / `800-1200ms`.
2. Hovering each chip opens a breakdown popover with per-component rows (LLM / STT / TTS / Voice Infra, + Knowledge Base only if the agent has a KB attached).
3. Type a paragraph into the prompt editor **without saving** — the Tokens and Cost numbers change as you type.
4. Switch the model dropdown (e.g. to Gemini 2.5 Pro) — the Cost and Latency numbers change.
5. Paste a very large prompt (>56,000 chars) — the Tokens popover shows the 14k hallucination-risk warning.
6. Numbers sanity: for a ~5k-token prompt on gemini-2.5-flash expect Cost ≈ $0.024–0.027/min, Latency 575–1025ms with a KB (500–900ms without).

- [ ] **Step 2: Full pre-commit sweep**

Run (repo root): `pre-commit run --all-files`
Expected: all hooks pass.

- [ ] **Step 3: Push and open PR**

```bash
cd /Users/evgenii.vasilenko/gofrolist/retell-clone
git push -u origin feat/agent-details-estimates
gh pr create \
  --title "feat: live cost/latency/token estimates in agent details header" \
  --body "$(cat <<'EOF'
Replaces the hardcoded Cost/Latency placeholders in the agent editor header
with live per-component estimate popovers (Retell-style): Cost $/min,
Latency ms range, and Token range, computed frontend-only from the draft
agent config against a sourced provider rate card (Gemini list prices,
Cartesia ink-whisper/sonic-2, LiveKit participant-minutes, as of 2026-07-14).

- New pure module `frontend/src/lib/estimates.ts` (rate card + estimators)
- New `HoverCard` pure-CSS popover primitive
- `MetaRow` now consumes the draft `llmView`, so numbers update while typing
- No backend or wire-contract changes

Spec: docs/superpowers/specs/2026-07-14-agent-details-estimates-design.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01HfAaqn1jdSbPo5w4FgUMrD
EOF
)"
```

Expected: PR created against `main`; the `pr-title` check passes (conventional `feat:` title).
