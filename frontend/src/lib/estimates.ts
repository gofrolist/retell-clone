// Pre-call cost / latency / token estimates for the agent editor header.
// Pure functions over the draft agent config — no fetches, no side effects.
//
// The rate card models our actual runtime pipeline (see worker/src/
// arhiteq_worker/main.py): Cartesia STT (ink-whisper) -> Gemini LLM ->
// Cartesia TTS (sonic-2), on LiveKit. All Cartesia voice ids resolve to
// Cartesia voices, so STT/TTS rates don't depend on the selected voice.
// A Gemini Live model replaces that whole pipeline with one speech-to-speech
// model, so those rows drop out — see `estimateCost`.
//
// Every constant is a provider list price or published figure as of
// 2026-07-14; update in place when providers reprice.

import { iterNodeEdges, type FlowNode } from "@/components/flow/flowModel";
import type { RawConversationFlow, RawLlm } from "@/lib/api";
import {
  DEFAULT_LIVE_MODEL,
  isLiveModel,
  type LlmModelId,
  RUNTIME_DEFAULT_MODEL,
} from "@/lib/models";
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

/**
 * What the estimator needs from an agent, normalized across the two agent
 * shapes so the rate card below has exactly one input type.
 *
 * A single-prompt agent carries its engine on a Retell LLM; a conversation-flow
 * agent has no LLM at all (`llm` comes back null from the control plane) and
 * keeps its engine on the flow's `model_choice` — the same split the worker
 * makes in `_build_session` (worker/src/arhiteq_worker/main.py). Estimating
 * straight off `RawLlm` therefore priced every flow agent as an LLM-less
 * Cartesia pipeline, no matter which model the flow actually ran.
 */
export interface EstimateInput {
  /** Engine id used at call time; drives every rate and latency lookup. */
  model: string;
  /** Row label for the prompt text sent on every turn. */
  promptLabel: string;
  /** Prompt text prepended to every turn. */
  promptText: string;
  /**
   * Context that varies turn to turn — one entry per flow node, each already
   * assembled the way the worker assembles it. A turn visits exactly ONE
   * node, so these bound a min/max range rather than summing. Empty for
   * single-prompt agents, whose whole prompt is fixed.
   */
  perNodeContext: string[];
  /** Tool definitions the agent can install. */
  tools: unknown[];
  /**
   * How many of `tools` reach the model at once. A single-prompt agent sends
   * `general_tools` in full ("all"); a flow's `tools` are a library that
   * function nodes resolve by `tool_id`, at most one per node ("one-of").
   */
  toolScope: "all" | "one-of";
  hasKb: boolean;
}

/** Estimator input for a single-prompt (Retell LLM) agent. */
export function llmEstimateInput(llm: RawLlm | null): EstimateInput | null {
  if (!llm) return null;
  return {
    model: llm.model,
    promptLabel: "System Prompt",
    promptText: (llm.general_prompt ?? "") + (llm.begin_message ?? ""),
    perNodeContext: [],
    tools: llm.general_tools ?? [],
    toolScope: "all",
    hasKb: (llm.knowledge_base_ids ?? []).length > 0,
  };
}

/**
 * What a flow node adds to the global prompt on the turn it is visited.
 *
 * Mirrors the worker's `node_instructions` + `prompt_edges` +
 * `transition_tool_schema` (worker/src/arhiteq_worker/flow.py):
 * - a `prompt` instruction contributes its text; a `static_text` one does
 *   NOT — that text is spoken verbatim through TTS and never reaches the
 *   model, so counting it would price words the LLM never sees;
 * - every offered transition is rendered into the prompt AND again into the
 *   `transition_to` tool schema's description, so its text lands twice. On a
 *   branchy node this outweighs the instruction itself.
 */
function nodeContextText(
  node: Record<string, unknown>,
  globalNodes: Record<string, unknown>[],
): string {
  const parts: string[] = [];

  const instruction = node.instruction as
    | { type?: unknown; text?: unknown }
    | null
    | undefined;
  if (instruction?.type === "prompt" && typeof instruction.text === "string") {
    parts.push(instruction.text);
  }

  const lines = promptEdgeLines(node, globalNodes);
  if (lines.length > 0) {
    const rendered = lines.join("\n");
    parts.push(`Available transitions:\n${rendered}`);
    // The tool schema repeats every id (as an enum) and every condition line.
    parts.push(rendered);
  }
  return parts.join("\n\n");
}

/**
 * `"<edge id>: <condition prompt>"` for each transition the model may choose.
 *
 * Mirrors `prompt_edges`: `always_edge`/`skip_response_edge` are the
 * runtime's own and never offered, non-`prompt` conditions are routed without
 * the model, dangling edges have nowhere to go — and each global node adds a
 * synthetic entry reachable from everywhere but itself.
 */
function promptEdgeLines(
  node: Record<string, unknown>,
  globalNodes: Record<string, unknown>[],
): string[] {
  const lines: string[] = [];
  for (const { shape, edge } of iterNodeEdges(node as FlowNode)) {
    if (shape === "always_edge" || shape === "skip_response_edge") continue;
    const condition = edge.transition_condition;
    if (!condition || condition.type !== "prompt") continue;
    if (!edge.destination_node_id) continue;
    const prompt = typeof condition.prompt === "string" ? condition.prompt : "";
    lines.push(`- ${edge.id ?? ""}: ${prompt}`);
  }
  for (const globalNode of globalNodes) {
    if (!globalNode.id || globalNode.id === node.id) continue;
    const setting = globalNode.global_node_setting as
      | { condition?: unknown }
      | null
      | undefined;
    const condition =
      typeof setting?.condition === "string" ? setting.condition : "";
    lines.push(`- global_${String(globalNode.id)}: ${condition}`);
  }
  return lines;
}

/** Estimator input for a conversation-flow agent. */
export function flowEstimateInput(
  flow: RawConversationFlow | null,
): EstimateInput | null {
  if (!flow) return null;
  // `model_choice` is Record<string, unknown> on the wire. An absent model is
  // NOT `DEFAULT_FLOW_MODEL` — that is only ever seeded into flows created in
  // this dashboard. An imported flow without one runs on the deployment
  // default, which is what `flow_model_id` -> `_gemini_model` resolves to.
  const chosen = (flow.model_choice as { model?: unknown } | null)?.model;
  const nodes = flow.nodes ?? [];
  const globalNodes = nodes.filter((n) => n.global_node_setting != null);
  return {
    model: typeof chosen === "string" && chosen ? chosen : RUNTIME_DEFAULT_MODEL,
    promptLabel: "Global Prompt",
    promptText: flow.global_prompt ?? "",
    perNodeContext: nodes
      .map((node) => nodeContextText(node, globalNodes))
      .filter((text) => text.length > 0),
    tools: flow.tools ?? [],
    toolScope: "one-of",
    hasKb: (flow.knowledge_base_ids ?? []).length > 0,
  };
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
  // Live/native-audio is billed on AUDIO tokens, not text turns: input
  // $3.00/1M, output $12.00/1M (paid tier,
  // https://ai.google.dev/gemini-api/docs/pricing, 2026-07-31 — both Live
  // models carry the same audio rate card; their text rates differ but Live
  // sessions are audio-to-audio). These rates feed GEMINI_LIVE_COST_PER_MIN
  // below — the token-turn path is skipped for Live — so this is the single
  // source of truth for the audio prices.
  "gemini-3.1-flash-live-preview": { inputPer1M: 3.0, outputPer1M: 12.0, ttftMs: [300, 700] },
  "gemini-live-2.5-flash-native-audio": { inputPer1M: 3.0, outputPer1M: 12.0, ttftMs: [300, 700] },
} satisfies Record<LlmModelId, LlmRate>;

// Gemini Live (native audio) replaces STT+LLM+TTS with one speech-to-speech
// model billed on audio tokens. Google tokenizes audio at 25 tokens/second
// (1,500 tokens/minute per stream), so the per-call-minute cost is: input for
// the whole minute the model listens to the caller, plus output for the share
// of the minute the agent actually speaks (~50%, matching the TTS assumption
// below). At $3/1M in + $12/1M out that is ~$0.0135/min — the old flat $0.60
// guess overstated it by ~40x. Derived from the rate-card entry above so the
// audio prices live in one place.
const LIVE_AUDIO_TOKENS_PER_MIN = 25 * 60;
const LIVE_AGENT_TALK_RATIO = 0.5;
const LIVE_RATE = LLM_RATES[DEFAULT_LIVE_MODEL];
const GEMINI_LIVE_COST_PER_MIN =
  (LIVE_AUDIO_TOKENS_PER_MIN / 1e6) * LIVE_RATE.inputPer1M +
  ((LIVE_AGENT_TALK_RATIO * LIVE_AUDIO_TOKENS_PER_MIN) / 1e6) * LIVE_RATE.outputPer1M;
const GEMINI_LIVE_LATENCY_MS: [number, number] = [300, 700];

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

const total = (rows: EstimateRow[]): Estimate => ({
  rows,
  min: rows.reduce((s, r) => s + r.min, 0),
  max: rows.reduce((s, r) => s + r.max, 0),
});

/** Per-turn prompt size. Null when there is no agent config to measure. */
export function estimateTokens(input: EstimateInput | null): Estimate | null {
  if (!input) return null;
  const promptTokens = tokensIn(input.promptText);
  const rows: EstimateRow[] = [
    { label: input.promptLabel, min: promptTokens, max: withHeadroom(promptTokens) },
  ];
  if (input.perNodeContext.length > 0) {
    // One node per turn, so the smallest and largest bound the range.
    // Summing would price a graph as if every node fired at once.
    const nodeTokens = input.perNodeContext.map(tokensIn);
    rows.push({
      label: "Node Context",
      min: Math.min(...nodeTokens),
      max: withHeadroom(Math.max(...nodeTokens)),
    });
  }
  if (input.tools.length > 0) {
    if (input.toolScope === "all") {
      const toolTokens = tokensIn(JSON.stringify(input.tools));
      rows.push({ label: "Tool Definitions", min: toolTokens, max: toolTokens });
    } else {
      // A library, not a payload: a function node installs the one entry its
      // `tool_id` names, and a node with no tool installs none. Serializing
      // the whole library would invent thousands of tokens no turn sends.
      const largest = Math.max(
        ...input.tools.map((tool) => tokensIn(JSON.stringify(tool))),
      );
      rows.push({ label: "Tool Definition", min: 0, max: largest });
    }
  }
  rows.push({
    label: "Conversation History",
    min: HISTORY_TOKENS[0],
    max: HISTORY_TOKENS[1],
  });
  if (input.hasKb) {
    rows.push({ label: "Knowledge Base", min: KB_TOKENS[0], max: KB_TOKENS[1] });
  }
  return total(rows);
}

const tokensIn = (text: string): number =>
  Math.ceil(text.length / CHARS_PER_TOKEN);

// +10% headroom: resolved {{variables}} usually expand the raw template.
// Integer math, not `n * 1.1` — 100 * 1.1 is 110.00000000000001 in binary
// floating point, which a ceil turns into a stray extra token.
const withHeadroom = (tokens: number): number => Math.ceil((tokens * 11) / 10);

/** USD per call minute. Cost rows are single values (min === max). */
export function estimateCost(
  input: EstimateInput | null,
  tokens: Estimate | null,
): Estimate {
  const rows: EstimateRow[] = [];
  // Gemini Live is one speech-to-speech model: no separate Cartesia STT/TTS,
  // and it's billed per audio minute rather than per text turn.
  if (input && isLiveModel(input.model)) {
    rows.push({
      label: `Gemini Live: ${input.model}`,
      min: GEMINI_LIVE_COST_PER_MIN,
      max: GEMINI_LIVE_COST_PER_MIN,
    });
    rows.push({ label: "Voice Infra", min: INFRA_COST_PER_MIN, max: INFRA_COST_PER_MIN });
    if (input.hasKb) {
      rows.push({ label: "Knowledge Base", min: KB_COST_PER_MIN, max: KB_COST_PER_MIN });
    }
    return total(rows);
  }
  if (input && tokens) {
    const rate = getLlmRate(input.model);
    const perMin =
      TURNS_PER_MIN *
      ((tokens.max / 1e6) * rate.inputPer1M +
        (OUTPUT_TOKENS_PER_TURN / 1e6) * rate.outputPer1M);
    rows.push({ label: `LLM: ${input.model}`, min: perMin, max: perMin });
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
  if (input?.hasKb) {
    rows.push({ label: "Knowledge Base", min: KB_COST_PER_MIN, max: KB_COST_PER_MIN });
  }
  return total(rows);
}

/** End-to-end turn latency range: sum of per-component ranges. */
export function estimateLatency(input: EstimateInput | null): Estimate {
  // Gemini Live handles transcription + generation + speech in one model, so
  // report a single realtime turn-latency band instead of STT + LLM + TTS.
  if (input && isLiveModel(input.model)) {
    const rows: EstimateRow[] = [
      {
        label: "Gemini Live (speech-to-speech)",
        min: GEMINI_LIVE_LATENCY_MS[0],
        max: GEMINI_LIVE_LATENCY_MS[1],
      },
    ];
    if (input.hasKb) {
      rows.push({ label: "Knowledge Base", min: KB_LATENCY_MS[0], max: KB_LATENCY_MS[1] });
    }
    return total(rows);
  }
  const rows: EstimateRow[] = [
    { label: "Transcription", min: STT_LATENCY_MS[0], max: STT_LATENCY_MS[1] },
  ];
  if (input) {
    const rate = getLlmRate(input.model);
    rows.push({ label: `LLM: ${input.model}`, min: rate.ttftMs[0], max: rate.ttftMs[1] });
  }
  rows.push({
    label: "TTS: cartesia sonic-2",
    min: TTS_LATENCY_MS[0],
    max: TTS_LATENCY_MS[1],
  });
  if (input?.hasKb) {
    rows.push({ label: "Knowledge Base", min: KB_LATENCY_MS[0], max: KB_LATENCY_MS[1] });
  }
  return total(rows);
}

// Representative input-token budget per turn for the model-picker cost badge.
// Roughly a small system prompt plus early conversation history — a fixed
// figure so each catalog model shows a stable, prompt-independent price.
const DISPLAY_INPUT_TOKENS_PER_TURN = 1500;

/**
 * Prompt-independent per-minute LLM cost for the model picker badge (mirrors
 * Retell's "$0.08/min" hint). This is the model's own token cost only — the
 * real per-agent figure (with STT/TTS/infra) is `estimateCost`. Live models
 * report their audio-minute cost.
 */
export function llmDisplayCostPerMin(model: string): number {
  if (isLiveModel(model)) return GEMINI_LIVE_COST_PER_MIN;
  const rate = getLlmRate(model);
  return (
    TURNS_PER_MIN *
    ((DISPLAY_INPUT_TOKENS_PER_TURN / 1e6) * rate.inputPer1M +
      (OUTPUT_TOKENS_PER_TURN / 1e6) * rate.outputPer1M)
  );
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
