/// <reference types="bun" />
import { describe, expect, test } from "bun:test";

import type { RawConversationFlow, RawLlm } from "@/lib/api";
import {
  estimateCost,
  estimateLatency,
  estimateTokens,
  flowEstimateInput,
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
    const cost = estimateCost(input, estimateTokens(input));

    expect(labels(cost.rows)).toEqual([
      "Gemini Live: gemini-live-2.5-flash-native-audio",
      "Voice Infra",
    ]);
    expect(labels(cost.rows).join()).not.toContain("cartesia");
    // 1,500 audio tokens/min in at $3/1M + 750 out at $12/1M + $0.001 infra.
    expect(cost.max).toBeCloseTo(0.0135 + 0.001, 6);
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
    const cost = estimateCost(input, estimateTokens(input));

    expect(labels(cost.rows)).toEqual([
      "Gemini Live: gemini-3.1-flash-live-preview",
      "Voice Infra",
    ]);
    expect(cost.max).toBeCloseTo(0.0135 + 0.001, 6);
  });

  // Live ids are marker-matched, so an id newer than the rate card still lands
  // on the Live path. It must fall back to an audio rate, not the text-model
  // default — that would under-price an audio minute by ~6x.
  test("an uncatalogued Live id still prices at the audio rate", () => {
    const input = flowEstimateInput(
      flow({ model_choice: { model: "gemini-9.9-flash-live-preview" } }),
    );
    const cost = estimateCost(input, estimateTokens(input));

    expect(labels(cost.rows)).toEqual(["Gemini Live: gemini-9.9-flash-live-preview", "Voice Infra"]);
    expect(cost.max).toBeCloseTo(0.0135 + 0.001, 6);
  });

  test("a pipeline flow keeps Cartesia and prices its chosen model", () => {
    const cheap = flowEstimateInput(
      flow({ model_choice: { model: "gemini-2.5-flash-lite" } }),
    );
    const dear = flowEstimateInput(
      flow({ model_choice: { model: "gemini-2.5-pro" } }),
    );

    expect(labels(estimateCost(cheap, estimateTokens(cheap)).rows)).toEqual([
      "LLM: gemini-2.5-flash-lite",
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
      "Voice Infra",
    ]);
    // The reported bug: switching the model changed nothing on screen.
    expect(estimateCost(dear, estimateTokens(dear)).max).toBeGreaterThan(
      estimateCost(cheap, estimateTokens(cheap)).max,
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
    expect(labels(estimateCost(input, estimateTokens(input)).rows)).toContain(
      "Knowledge Base",
    );
    expect(labels(estimateLatency(input).rows)).toContain("Knowledge Base");
    expect(labels(estimateTokens(input)!.rows)).toContain("Knowledge Base");
  });
});

describe("single-prompt agents are unchanged", () => {
  test("a pipeline LLM keeps the STT -> LLM -> TTS card", () => {
    const input = llmEstimateInput(llm());
    expect(labels(estimateCost(input, estimateTokens(input)).rows)).toEqual([
      "LLM: gemini-2.5-flash",
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
      "Voice Infra",
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
    expect(labels(estimateCost(input, estimateTokens(input)).rows).join()).not.toContain(
      "cartesia",
    );
  });

  test("a null config still yields the bare pipeline card", () => {
    expect(estimateTokens(null)).toBeNull();
    expect(labels(estimateCost(null, null).rows)).toEqual([
      "STT: cartesia ink-whisper",
      "TTS: cartesia sonic-2",
      "Voice Infra",
    ]);
  });
});
