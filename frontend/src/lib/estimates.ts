// Pre-call cost / latency / token estimates for the agent editor header.
// Pure functions over the draft agent config — no fetches, no side effects.
//
// The estimator models our actual runtime pipeline (see worker/src/
// arhiteq_worker/main.py): Cartesia STT (ink-whisper) -> Gemini LLM ->
// Cartesia TTS (sonic-2), on LiveKit. All Cartesia voice ids resolve to
// Cartesia voices, so STT/TTS rates don't depend on the selected voice.
// A Gemini Live model replaces that whole pipeline with one speech-to-speech
// model, so those rows drop out — see `estimateCost`.
//
// Dollar figures are CUSTOMER PRICES, not our costs: they come from the
// backend's pricing endpoint (already marked up), with a compiled-in
// fallback below for when that endpoint is unreachable. Every caller passes
// a `PriceCard`; see `FALLBACK_PRICES` for how the fallback is kept honest.
// Latency figures have no backend equivalent (the API doesn't measure or
// price them) and stay local constants, sourced from provider-published
// figures as of 2026-07-14; update in place when providers republish.

import { iterNodeEdges, type FlowNode } from "@/components/flow/flowModel";
import type { RawConversationFlow, RawLlm } from "@/lib/api";
import { isLiveModel, type LlmModelId, RUNTIME_DEFAULT_MODEL } from "@/lib/models";
import type { PriceCard, PricedModel } from "@/lib/types";
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

interface LlmLatency {
  ttftMs: [number, number]; // time-to-first-token; our estimate, Google publishes none
}

// `satisfies` ties this to the models.ts catalog: adding a model there
// without a latency entry here is a compile error (unknown wire ids still
// fall back to a default at runtime, via `getLlmLatency`).
const LLM_LATENCY = {
  "gemini-3.5-flash": { ttftMs: [350, 600] },
  "gemini-3.1-flash-lite": { ttftMs: [250, 450] },
  "gemini-2.5-pro": { ttftMs: [600, 1200] },
  "gemini-2.5-flash": { ttftMs: [350, 600] },
  "gemini-2.5-flash-lite": { ttftMs: [250, 450] },
  "gemini-3.1-flash-live-preview": { ttftMs: [300, 700] },
  "gemini-live-2.5-flash-native-audio": { ttftMs: [300, 700] },
} satisfies Record<LlmModelId, LlmLatency>;

const GEMINI_LIVE_LATENCY_MS: [number, number] = [300, 700];

// Catalog drift safety net: unknown model ids estimate as gemini-2.5-flash.
const DEFAULT_LLM_LATENCY: LlmLatency = LLM_LATENCY["gemini-2.5-flash"];

// The wire `model` is a free-form string, so guard the lookup with
// Object.hasOwn: a bare index would resolve Object.prototype keys (a model
// named "toString" would return the inherited function and crash the math).
function getLlmLatency(model: string): LlmLatency {
  return Object.hasOwn(LLM_LATENCY, model)
    ? (LLM_LATENCY as Record<string, LlmLatency>)[model]
    : DEFAULT_LLM_LATENCY;
}

/**
 * Last-known PRICES (not costs), compiled in so the editor still estimates
 * when the pricing endpoint is unreachable. Showing a zero, an error, or our
 * cost to a customer are all worse outcomes than showing a slightly stale
 * price — so the fallback is deliberately conservative.
 *
 * Captured from the seeded pricing catalog itself (backend/src/arhiteq_api/
 * services/pricing_seed.py MODEL_COSTS / COMPONENT_COSTS, at the seeded 300%
 * global markup) via `services/pricing.py`'s `model_prices()` — not
 * hand-computed — so this matches what GET /dashboard/pricing/models actually
 * serves on a freshly seeded workspace. Refresh this whenever the markup rule
 * or the underlying provider costs change.
 *
 * `satisfies` keeps the compile-time guarantee the old LLM_RATES had: adding
 * a model to the catalog without a price is a build error, not a runtime
 * surprise.
 */
export const FALLBACK_PRICES = {
  models: [
    { model_id: "gemini-3.5-flash", is_audio: false, per_minute: 0.1224 },
    { model_id: "gemini-3.1-flash-lite", is_audio: false, per_minute: 0.0744 },
    { model_id: "gemini-2.5-pro", is_audio: false, per_minute: 0.1188 },
    { model_id: "gemini-2.5-flash", is_audio: false, per_minute: 0.078 },
    { model_id: "gemini-2.5-flash-lite", is_audio: false, per_minute: 0.06816 },
    { model_id: "gemini-3.1-flash-live-preview", is_audio: true, per_minute: 0.054 },
    { model_id: "gemini-live-2.5-flash-native-audio", is_audio: true, per_minute: 0.054 },
  ],
  components: { cartesia_stt: 0.0088, cartesia_tts: 0.056, kb_overhead: 0.004 },
  unpriced: [],
} satisfies PriceCard;

/**
 * Resolve one model's price card entry, falling back in order: the supplied
 * card, the compiled-in card (covers a model the backend left in `unpriced`
 * or omitted for any other reason), then a catalog-drift default for an id
 * neither card has ever heard of.
 */
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

// Assumption-based turn/prompt model — our own estimates, no external source.
const CHARS_PER_TOKEN = 4; // standard rough heuristic for English text
const HISTORY_TOKENS: [number, number] = [250, 2200]; // grows over the call
const KB_TOKENS: [number, number] = [200, 1500]; // retrieved chunks per turn

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

/**
 * USD per call minute. Cost rows are single values (min === max).
 *
 * `tokens` no longer feeds the dollar math (see the LLM-row comment below);
 * it stays a parameter for signature/call-site stability and because the
 * caller always has it on hand from `estimateTokens(input)`.
 */
export function estimateCost(
  input: EstimateInput | null,
  tokens: Estimate | null,
  prices: PriceCard,
): Estimate {
  const rows: EstimateRow[] = [];
  const stt = prices.components?.cartesia_stt ?? FALLBACK_PRICES.components.cartesia_stt;
  const tts = prices.components?.cartesia_tts ?? FALLBACK_PRICES.components.cartesia_tts;
  const kb = prices.components?.kb_overhead ?? FALLBACK_PRICES.components.kb_overhead;

  if (!input) {
    rows.push({ label: "STT: cartesia ink-whisper", min: stt, max: stt });
    rows.push({ label: "TTS: cartesia sonic-2", min: tts, max: tts });
    return total(rows);
  }

  // The headline covers MODEL + STT + TTS and nothing else: the backend's
  // `cost_per_min_stack` is `model + cartesia_stt + cartesia_tts` for a text
  // model and `model` alone for an audio one (services/pricing.py), and
  // `per_minute` is that stack marked up. So the pipeline rows must sum back
  // to it, while the knowledge base — which the stack never priced — is an
  // ADDITIONAL charge that pushes the total above the headline.
  //
  // Re-deriving the LLM row from marked-up token rates would drift from
  // `per_minute` whenever a model carries an explicit price or a fixed adder,
  // neither of which is expressible as a per-token markup — and a breakdown
  // that does not add up is worse than no breakdown.
  const headline = priced(input.model, prices).per_minute;

  // Gemini Live is one speech-to-speech model: no separate Cartesia STT/TTS,
  // and it's billed per audio minute rather than per text turn. Its stack has
  // no synthesis leg, so the headline IS the model row.
  if (isLiveModel(input.model)) {
    rows.push({ label: `Gemini Live: ${input.model}`, min: headline, max: headline });
    if (input.hasKb) {
      rows.push({ label: "Knowledge Base", min: kb, max: kb });
    }
    return total(rows);
  }

  // Since price = (model + stt + tts) x markup and the component rows are the
  // same components at the same markup, this is the model's own marked-up
  // price and cannot go negative on a self-consistent card — a test pins that
  // for every model in FALLBACK_PRICES. The clamp only guards a live card
  // whose components outrun its model prices, where a negative row on screen
  // would be worse than a flat one.
  const unclamped = headline - stt - tts;
  if (unclamped < 0) {
    // The clamp is silent by design (a flat $0 row beats a negative one), but
    // a live backend card that reaches it is a pricing-data bug: the row will
    // read "$0.000/min" to a customer, which looks like a deliberate free
    // tier rather than the error it is. Surface it so it's caught in the
    // field instead of silently under-billing.
    console.warn(
      `[estimateCost] LLM row clamped to $0 for model "${input.model}": ` +
        `headline $${headline.toFixed(6)}/min - stt $${stt.toFixed(6)}/min - ` +
        `tts $${tts.toFixed(6)}/min = $${unclamped.toFixed(6)}/min`,
    );
  }
  const llmRow = Math.max(unclamped, 0);
  rows.push({ label: `LLM: ${input.model}`, min: llmRow, max: llmRow });
  rows.push({ label: "STT: cartesia ink-whisper", min: stt, max: stt });
  rows.push({ label: "TTS: cartesia sonic-2", min: tts, max: tts });
  if (input.hasKb) {
    rows.push({ label: "Knowledge Base", min: kb, max: kb });
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
    const latency = getLlmLatency(input.model);
    rows.push({ label: `LLM: ${input.model}`, min: latency.ttftMs[0], max: latency.ttftMs[1] });
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

/**
 * Prompt-independent per-minute LLM price for the model picker badge (mirrors
 * Retell's "$0.08/min" hint). This is the model's own price only — the real
 * per-agent figure (with STT/TTS/KB) is `estimateCost`. `per_minute` is
 * already prompt-independent (the backend prices it off a fixed display token
 * budget it does not disclose), so this is a thin wrapper over `priced()` for
 * both text and Live models alike.
 */
export function llmDisplayCostPerMin(model: string, prices: PriceCard): number {
  return priced(model, prices).per_minute;
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
