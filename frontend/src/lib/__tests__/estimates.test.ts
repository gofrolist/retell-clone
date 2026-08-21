/// <reference types="bun" />
import { describe, expect, test } from "bun:test";

import type { RawConversationFlow, RawLlm } from "@/lib/api";
import {
  estimateCost,
  estimateLatency,
  estimateTokens,
  FALLBACK_PRICES,
  flowEstimateInput,
  llmDisplayCostPerMin,
  llmEstimateInput,
} from "@/lib/estimates";
import { RUNTIME_DEFAULT_MODEL } from "@/lib/models";

/**
 * Agent-details estimates for both agent shapes.
 *
 * A conversation-flow agent has no Retell LLM — its engine lives on the flow's
 * `model_choice`, exactly as the worker reads it in `_build_session`. The
 * editor used to price flows straight off the (always null) LLM, so every flow
 * agent showed the same fixed Cartesia STT+TTS+infra card no matter which
 * model the flow ran, including Gemini Live, which uses no Cartesia at all.
 */

const flow = (over: Partial<RawConversationFlow> = {}): RawConversationFlow =>
  ({
    conversation_flow_id: "cf_1",
    version: 1,
    global_prompt: "Be brief.",
    nodes: [
      { id: "start", instruction: { type: "static_text", text: "Hi there!" } },
      { id: "end", instruction: { type: "static_text", text: "Goodbye." } },
    ],
    model_choice: { type: "cascading", model: "gemini-2.5-flash" },
    ...over,
  }) as RawConversationFlow;

const llm = (over: Partial<RawLlm> = {}): RawLlm =>
  ({
    llm_id: "llm_1",
    model: "gemini-2.5-flash",
    model_temperature: 0,
    general_prompt: "You are a helpful agent.",
    begin_message: "Hi!",
    start_speaker: "agent",
    general_tools: [],
    ...over,
  }) as RawLlm;

const labels = (rows: { label: string }[]) => rows.map((r) => r.label);

describe("flow agents price off the flow's own model", () => {
  test("a Gemini Live flow bills audio minutes, with no Cartesia rows", () => {
    const input = flowEstimateInput(
      flow({ model_choice: { model: "gemini-live-2.5-flash-native-audio" } }),
    );
    const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);

    expect(labels(cost.rows)).toEqual(["Gemini Live: gemini-live-2.5-flash-native-audio"]);
    expect(labels(cost.rows).join()).not.toContain("cartesia");
    // Derived here, not read back out of the card: an audio minute is
    // 25 tok/s x 60 = 1500 input tokens plus 50% of that spoken back, at
    // $3/$12 per 1M, = $0.0135 cost, x4 markup = $0.054 PRICE. Computing the
    // expectation with `llmDisplayCostPerMin` would pass for any card.
    expect(cost.max).toBeCloseTo(0.054, 6);
  });

  test("a Gemini Live flow reports one speech-to-speech latency band", () => {
    const input = flowEstimateInput(
      flow({ model_choice: { model: "gemini-live-2.5-flash-native-audio" } }),
    );
    expect(labels(estimateLatency(input).rows)).toEqual([
      "Gemini Live (speech-to-speech)",
    ]);
  });

  test("the 3.1 Live preview prices as Live, same audio rate card as 2.5", () => {
    const input = flowEstimateInput(
      flow({ model_choice: { model: "gemini-3.1-flash-live-preview" } }),
    );
    const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);

    expect(labels(cost.rows)).toEqual(["Gemini Live: gemini-3.1-flash-live-preview"]);
    // The property is "same rate card as 2.5", so compare the two Live ids to
    // each other. Comparing 3.1 against its own price would hold whatever the
    // card said 3.1 cost.
    expect(cost.max).toBeCloseTo(
      llmDisplayCostPerMin("gemini-live-2.5-flash-native-audio", FALLBACK_PRICES),
      6,
    );
  });

  // Live ids are marker-matched, so an id newer than the rate card still lands
  // on the Live path. It must fall back to an audio rate, not the text-model
  // default — that would under-price an audio minute by ~6x.
  test("an uncatalogued Live id still prices at the audio rate", () => {
    const input = flowEstimateInput(
      flow({ model_choice: { model: "gemini-9.9-flash-live-preview" } }),
    );
    const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);

    expect(labels(cost.rows)).toEqual(["Gemini Live: gemini-9.9-flash-live-preview"]);
    // No rate for this id, so it must fall back to a Live rate, not text.
    expect(cost.max).toBeCloseTo(
      llmDisplayCostPerMin("gemini-3.1-flash-live-preview", FALLBACK_PRICES),
      6,
    );
  });

  test("a pipeline flow keeps Cartesia and prices its chosen model", () => {
    const cheap = flowEstimateInput(
      flow({ model_choice: { model: "gemini-2.5-flash-lite" } }),
    );
    const dear = flowEstimateInput(
      flow({ model_choice: { model: "gemini-2.5-pro" } }),
    );

    expect(labels(estimateCost(cheap, estimateTokens(cheap), FALLBACK_PRICES).rows)).toEqual([
      "LLM: gemini-2.5-flash-lite",
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
    ]);
    // The reported bug: switching the model changed nothing on screen.
    expect(estimateCost(dear, estimateTokens(dear), FALLBACK_PRICES).max).toBeGreaterThan(
      estimateCost(cheap, estimateTokens(cheap), FALLBACK_PRICES).max,
    );
  });

  test("an unset model_choice falls back to what the worker runs", () => {
    // NOT DEFAULT_FLOW_MODEL: that is a UI seed for flows created here, so
    // naming it would label an imported flow with a model it never runs.
    expect(flowEstimateInput(flow({ model_choice: null }))?.model).toBe(
      RUNTIME_DEFAULT_MODEL,
    );
  });

  test("tokens count the global prompt plus one node, not every node", () => {
    const input = flowEstimateInput(
      flow({
        global_prompt: "G".repeat(400),
        nodes: [
          { id: "a", instruction: { type: "prompt", text: "x".repeat(40) } },
          { id: "b", instruction: { type: "prompt", text: "y".repeat(400) } },
        ],
      }),
    );
    const rows = estimateTokens(input)!.rows;

    expect(labels(rows)).toEqual([
      "Global Prompt",
      "Node Context",
      "Conversation History",
    ]);
    const node = rows.find((r) => r.label === "Node Context")!;
    expect(node.min).toBe(10); // 40 chars / 4
    expect(node.max).toBe(110); // 400 chars / 4, +10% headroom
  });

  test("static_text nodes contribute nothing — TTS speaks them, not the model", () => {
    // The default new-flow graph is entirely static_text ("Hi! How can I help
    // you today?" / "Goodbye."), so counting it billed words the LLM never
    // sees, on every brand-new agent.
    const input = flowEstimateInput(
      flow({
        nodes: [
          { id: "a", instruction: { type: "static_text", text: "x".repeat(4000) } },
        ],
      }),
    );
    expect(input?.perNodeContext).toEqual([]);
    expect(labels(estimateTokens(input)!.rows)).toEqual([
      "Global Prompt",
      "Conversation History",
    ]);
  });

  test("offered transitions are counted — twice, as prompt and tool schema", () => {
    const withEdge = flowEstimateInput(
      flow({
        nodes: [
          {
            id: "a",
            instruction: { type: "prompt", text: "Ask." },
            edges: [
              {
                id: "e1",
                destination_node_id: "b",
                transition_condition: { type: "prompt", prompt: "P".repeat(200) },
              },
            ],
          },
        ],
      }),
    );
    const bare = flowEstimateInput(
      flow({ nodes: [{ id: "a", instruction: { type: "prompt", text: "Ask." } }] }),
    );
    // The condition text lands in the prompt AND the transition_to schema.
    expect(estimateTokens(withEdge)!.max).toBeGreaterThan(
      estimateTokens(bare)!.max + 100,
    );
  });

  test("transitions the model never chooses are not counted", () => {
    // always_edge is the runtime's own, an equation condition is evaluated in
    // code, and a dangling edge has nowhere to send the call.
    const input = flowEstimateInput(
      flow({
        nodes: [
          {
            id: "a",
            instruction: { type: "prompt", text: "Ask." },
            always_edge: {
              id: "always",
              destination_node_id: "b",
              transition_condition: { type: "prompt", prompt: "Z".repeat(400) },
            },
            edges: [
              {
                id: "eq",
                destination_node_id: "b",
                transition_condition: { type: "equation", equations: [] },
              },
              {
                id: "dangling",
                transition_condition: { type: "prompt", prompt: "Y".repeat(400) },
              },
            ],
          },
        ],
      }),
    );
    expect(input?.perNodeContext).toEqual(["Ask."]);
  });

  test("a global node is offered from everywhere but itself", () => {
    const input = flowEstimateInput(
      flow({
        nodes: [
          { id: "a", instruction: { type: "prompt", text: "Ask." } },
          {
            id: "g",
            instruction: { type: "prompt", text: "Help." },
            global_node_setting: { condition: "caller asks for a human" },
          },
        ],
      }),
    );
    expect(input?.perNodeContext[0]).toContain("caller asks for a human");
    expect(input?.perNodeContext[1]).not.toContain("caller asks for a human");
  });

  test("flow tools are a library: at most one node installs one", () => {
    const input = flowEstimateInput(
      flow({
        tools: [
          { tool_id: "t1", name: "a", description: "d".repeat(400) },
          { tool_id: "t2", name: "b", description: "d".repeat(4000) },
        ],
      }),
    );
    const row = estimateTokens(input)!.rows.find(
      (r) => r.label === "Tool Definition",
    )!;
    // A node with no tool installs none; the largest single entry is the cap.
    // Serializing the whole library would invent ~1,100 phantom tokens.
    expect(row.min).toBe(0);
    expect(row.max).toBeLessThan(1100);
    expect(row.max).toBeGreaterThan(1000);
  });

  test("an attached knowledge base adds its rows", () => {
    const input = flowEstimateInput(flow({ knowledge_base_ids: ["kb_1"] }));
    const bare = flowEstimateInput(flow());
    expect(
      labels(estimateCost(input, estimateTokens(input), FALLBACK_PRICES).rows),
    ).toContain("Knowledge Base");
    // A row that does not move the total is decoration. It used to be one.
    expect(estimateCost(input, estimateTokens(input), FALLBACK_PRICES).max).toBeGreaterThan(
      estimateCost(bare, estimateTokens(bare), FALLBACK_PRICES).max,
    );
    expect(labels(estimateLatency(input).rows)).toContain("Knowledge Base");
    expect(labels(estimateTokens(input)!.rows)).toContain("Knowledge Base");
  });
});

describe("single-prompt agents are unchanged", () => {
  test("a pipeline LLM keeps the STT -> LLM -> TTS card", () => {
    const input = llmEstimateInput(llm());
    expect(labels(estimateCost(input, estimateTokens(input), FALLBACK_PRICES).rows)).toEqual([
      "LLM: gemini-2.5-flash",
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
    ]);
    expect(labels(estimateTokens(input)!.rows)).toEqual([
      "System Prompt",
      "Conversation History",
    ]);
  });

  test("tool definitions add a row", () => {
    const input = llmEstimateInput(
      llm({ general_tools: [{ name: "check_calendar" }] }),
    );
    expect(labels(estimateTokens(input)!.rows)).toContain("Tool Definitions");
  });

  test("a Live LLM drops Cartesia just like a Live flow", () => {
    const input = llmEstimateInput(
      llm({ model: "gemini-live-2.5-flash-native-audio" }),
    );
    expect(
      labels(estimateCost(input, estimateTokens(input), FALLBACK_PRICES).rows).join(),
    ).not.toContain("cartesia");
  });

  test("a null config still yields the bare pipeline card", () => {
    expect(estimateTokens(null)).toBeNull();
    expect(labels(estimateCost(null, null, FALLBACK_PRICES).rows)).toEqual([
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
    ]);
  });
});

const LIVE = "gemini-live-2.5-flash-native-audio";
const TEXT = "gemini-2.5-flash-lite";

/**
 * Our real per-1M-token COSTS, from backend/src/arhiteq_api/services/
 * pricing_seed.py MODEL_COSTS. Every number compiled into FALLBACK_PRICES is
 * a marked-up price; these are the floor those prices must clear. Kept as a
 * literal map (not derived from the card) so that a card edited down to cost
 * has nothing to hide behind.
 */
const MODEL_COSTS_PER_1M: Record<string, { input: number; output: number }> = {
  "gemini-3.5-flash": { input: 1.5, output: 9.0 },
  "gemini-3.1-flash-lite": { input: 0.25, output: 1.5 },
  "gemini-2.5-pro": { input: 1.25, output: 10.0 },
  "gemini-2.5-flash": { input: 0.3, output: 2.5 },
  "gemini-2.5-flash-lite": { input: 0.1, output: 0.4 },
  "gemini-3.1-flash-live-preview": { input: 3.0, output: 12.0 },
  "gemini-live-2.5-flash-native-audio": { input: 3.0, output: 12.0 },
};

/** COMPONENT_COSTS from the same seed. */
const COMPONENT_COSTS = { cartesia_stt: 0.0022, cartesia_tts: 0.014, kb_overhead: 0.001 };

/** Live audio minute at seed cost: 1500 in + 750 out tokens at $3/$12 per 1M. */
const LIVE_AUDIO_COST_PER_MIN = 0.0135;

/**
 * Our cost for one call minute of `model`, using the same two formulas the
 * backend uses (services/pricing.py `cost_stack`): an audio model bills the
 * stream, a text model bills turns and carries the Cartesia legs.
 */
function costPerMin(modelId: string, isAudio: boolean): number {
  const a = FALLBACK_PRICES.assumptions;
  const { input, output } = MODEL_COSTS_PER_1M[modelId];
  const tokensPerMin = a.audio_tokens_per_sec * 60;
  if (isAudio) {
    return (
      (tokensPerMin / 1e6) * input + ((a.agent_talk_ratio * tokensPerMin) / 1e6) * output
    );
  }
  const model =
    a.turns_per_min *
    ((a.display_input_tokens_per_turn / 1e6) * input +
      (a.output_tokens_per_turn / 1e6) * output);
  return model + COMPONENT_COSTS.cartesia_stt + COMPONENT_COSTS.cartesia_tts;
}

describe("price card", () => {
  test("quotes above cost for every model in the fallback", () => {
    // A stale fallback must never quote below cost — it is what renders when
    // the pricing endpoint is unreachable. Driven off the card's own model
    // list, so all seven are covered and a newly added model with no cost
    // entry here fails rather than slipping through unchecked.
    expect(FALLBACK_PRICES.models.length).toBe(
      Object.keys(MODEL_COSTS_PER_1M).length,
    );
    for (const m of FALLBACK_PRICES.models) {
      expect(MODEL_COSTS_PER_1M[m.model_id]).toBeDefined();
      const cost = costPerMin(m.model_id, m.is_audio);
      expect(llmDisplayCostPerMin(m.model_id, FALLBACK_PRICES)).toBeGreaterThan(cost);
      // The per-token figures are prices too, and the picker shows them.
      expect(m.input_per_1m).toBeGreaterThan(MODEL_COSTS_PER_1M[m.model_id].input);
      expect(m.output_per_1m).toBeGreaterThan(MODEL_COSTS_PER_1M[m.model_id].output);
    }
  });

  test("quotes the components above cost too", () => {
    // STT/TTS/KB are billed to the customer as their own rows, so a cost-level
    // component would sell the pipeline at cost even with every model marked up.
    for (const [name, cost] of Object.entries(COMPONENT_COSTS)) {
      const price = FALLBACK_PRICES.components[name as keyof typeof COMPONENT_COSTS];
      expect(price).toBeGreaterThan(cost);
    }
  });

  test("the LLM row never clamps to zero, with or without a knowledge base", () => {
    // The regression: the KB price used to be subtracted from a headline that
    // never contained it, driving gemini-2.5-flash-lite's LLM row negative and
    // rendering "$0.000/min". The row must now be the model's own marked-up
    // price — strictly above its model-only cost — for every model, KB or not.
    for (const m of FALLBACK_PRICES.models) {
      for (const hasKb of [false, true]) {
        const input = llmEstimateInput(
          llm({ model: m.model_id, knowledge_base_ids: hasKb ? ["kb_1"] : [] }),
        );
        const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);
        const modelRow = cost.rows.find((r) => r.label.endsWith(m.model_id))!;
        const modelOnlyCost = m.is_audio
          ? LIVE_AUDIO_COST_PER_MIN
          : costPerMin(m.model_id, false) -
            COMPONENT_COSTS.cartesia_stt -
            COMPONENT_COSTS.cartesia_tts;
        expect(modelRow.min).toBeGreaterThan(modelOnlyCost);
      }
    }
  });

  test("a knowledge base costs extra — the headline never contained it", () => {
    // The backend's cost_per_min_stack is model + stt + tts, with no KB term,
    // so KB is charged on top. Before the fix the two totals were identical
    // for every model and attaching a knowledge base was free.
    const kbPrice = FALLBACK_PRICES.components.kb_overhead;
    for (const model of [TEXT, LIVE]) {
      const bare = llmEstimateInput(llm({ model }));
      const withKb = llmEstimateInput(llm({ model, knowledge_base_ids: ["kb_1"] }));
      const bareCost = estimateCost(bare, estimateTokens(bare), FALLBACK_PRICES);
      const kbCost = estimateCost(withKb, estimateTokens(withKb), FALLBACK_PRICES);

      expect(kbCost.max).toBeGreaterThan(bareCost.max);
      expect(kbCost.max - bareCost.max).toBeCloseTo(kbPrice, 6);
      // …and above the headline, which priced no retrieval at all.
      expect(kbCost.max).toBeGreaterThan(llmDisplayCostPerMin(model, FALLBACK_PRICES));
    }
  });

  test("uses the supplied price card rather than the fallback", () => {
    const doubled: typeof FALLBACK_PRICES = {
      ...FALLBACK_PRICES,
      models: FALLBACK_PRICES.models.map((m) => ({ ...m, per_minute: m.per_minute * 2 })),
    };
    expect(llmDisplayCostPerMin(LIVE, doubled)).toBeCloseTo(
      llmDisplayCostPerMin(LIVE, FALLBACK_PRICES) * 2,
    );
  });

  test("falls back for a model missing from the card", () => {
    // `> 0` would be satisfied by our raw cost, which is exactly what must
    // never reach a customer — so pin it above the Live audio minute's cost.
    const empty = { ...FALLBACK_PRICES, models: [] };
    expect(llmDisplayCostPerMin(LIVE, empty)).toBeGreaterThan(LIVE_AUDIO_COST_PER_MIN);
  });

  test("breaks the headline down into rows that sum back to it", () => {
    // The TEXT path, where the subtraction actually happens: LLM + STT + TTS
    // must land back on the headline. A Live model has a single row, so it
    // would only assert that a total equals itself.
    const input = llmEstimateInput({ llm_id: "llm_1", model: TEXT } as RawLlm);
    const cost = estimateCost(input, estimateTokens(input), FALLBACK_PRICES);
    expect(cost.rows).toHaveLength(3);
    expect(cost.max).toBeCloseTo(llmDisplayCostPerMin(TEXT, FALLBACK_PRICES), 6);

    // The KB path is the exception: it is charged on top of the headline.
    const kbInput = llmEstimateInput(
      llm({ model: TEXT, knowledge_base_ids: ["kb_1"] }),
    );
    const kbCost = estimateCost(kbInput, estimateTokens(kbInput), FALLBACK_PRICES);
    expect(kbCost.max).toBeCloseTo(
      llmDisplayCostPerMin(TEXT, FALLBACK_PRICES) + FALLBACK_PRICES.components.kb_overhead,
      6,
    );
    expect(kbCost.max).toBeGreaterThan(cost.max);
  });

  test("adds the fixed per-minute adder after the token math", () => {
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
