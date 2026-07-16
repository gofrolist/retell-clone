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
import type { LlmModelId } from "@/lib/models";
import { formatCost } from "@/lib/utils";

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
// `satisfies` ties this to the models.ts catalog: adding a model there
// without a rate here is a compile error.
const LLM_RATES = {
  "gemini-3.5-flash": { inputPer1M: 1.5, outputPer1M: 9.0, ttftMs: [350, 600] },
  "gemini-3.1-flash-lite": { inputPer1M: 0.25, outputPer1M: 1.5, ttftMs: [250, 450] },
  "gemini-2.5-pro": { inputPer1M: 1.25, outputPer1M: 10.0, ttftMs: [600, 1200] },
  "gemini-2.5-flash": { inputPer1M: 0.3, outputPer1M: 2.5, ttftMs: [350, 600] },
  "gemini-2.5-flash-lite": { inputPer1M: 0.1, outputPer1M: 0.4, ttftMs: [250, 450] },
} satisfies Record<LlmModelId, LlmRate>;

// Catalog drift safety net: unknown model ids estimate as gemini-2.5-flash.
const DEFAULT_LLM_RATE: LlmRate = LLM_RATES["gemini-2.5-flash"];

// The wire `model` is a free-form string, so guard the lookup with
// Object.hasOwn: a bare index would resolve Object.prototype keys (a model
// named "toString" would return the inherited function and crash the math).
function getLlmRate(model: string): LlmRate {
  return Object.hasOwn(LLM_RATES, model)
    ? (LLM_RATES as Record<string, LlmRate>)[model]
    : DEFAULT_LLM_RATE;
}

// Assumption-based turn/prompt model — our own estimates, no external source.
const CHARS_PER_TOKEN = 4; // standard rough heuristic for English text
const TURNS_PER_MIN = 4; // assumed LLM requests per call minute
const OUTPUT_TOKENS_PER_TURN = 150; // assumed visible tokens per response
const HISTORY_TOKENS: [number, number] = [250, 2200]; // grows over the call
const KB_TOKENS: [number, number] = [200, 1500]; // retrieved chunks per turn

// Cartesia (https://docs.cartesia.ai/pricing, Scale tier, 2026-07-14):
// STT ink-whisper 1 credit/sec realtime at $37.375/M credits ~= $0.0022/min.
// TTS ~1 credit/char, ~750 chars/min of speech ~= $0.028/min of speech;
// agent speaks ~50% of a call minute -> $0.014 per call minute.
const STT_COST_PER_MIN = 0.0022;
const TTS_COST_PER_MIN = 0.014;
// LiveKit Cloud (https://livekit.com/pricing, 2026-07-14): $0.0005 per
// participant-minute overage x 2 connections (caller + worker).
const INFRA_COST_PER_MIN = 0.001;
// Embedding/retrieval overhead when a knowledge base is attached — our own rounded estimate, no external source.
const KB_COST_PER_MIN = 0.001;

// Latency figures (ms). STT: Cartesia's published ink-whisper streaming
// benchmark, median 66 / P90 98
// (https://www.cartesia.ai/blog/introducing-ink-speech-to-text). TTS:
// published sub-90ms sonic-2 model TTFB plus network headroom
// (https://www.cartesia.ai/pricing). KB: retrieval round-trip — our own
// estimate, no external source.
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
    const rate = getLlmRate(llm.model);
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
    const rate = getLlmRate(llm.model);
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
  return `${formatCost(v)}/min`;
}

export function formatTokenValue(n: number): string {
  // M tier starts where the k tier would round to "1000.0k".
  if (n >= 999_950) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
