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
import { DEFAULT_FLOW_MODEL } from "@/lib/models";

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

  test("an unset model_choice falls back to the flow default", () => {
    expect(flowEstimateInput(flow({ model_choice: null }))?.model).toBe(
      DEFAULT_FLOW_MODEL,
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
      "Node Instruction",
      "Conversation History",
    ]);
    const node = rows.find((r) => r.label === "Node Instruction")!;
    expect(node.min).toBe(10); // 40 chars / 4
    // 400 chars / 4 = 100, +10% headroom. 111 rather than 110 because
    // 100 * 1.1 is 110.00000000000001 in binary floating point and the
    // headroom is a ceil — one token either way is inside the noise.
    expect(node.max).toBe(111);
  });

  test("nodes without instruction text are skipped, not counted as empty", () => {
    const input = flowEstimateInput(
      flow({
        nodes: [
          { id: "a", type: "end" },
          { id: "b", instruction: { type: "prompt", text: "abcd" } },
        ],
      }),
    );
    expect(input?.nodeInstructions).toEqual(["abcd"]);
    const node = estimateTokens(input)!.rows.find(
      (r) => r.label === "Node Instruction",
    )!;
    expect(node.min).toBe(1);
  });

  test("a flow with no instruction text anywhere omits the node row", () => {
    const input = flowEstimateInput(flow({ nodes: [{ id: "a", type: "end" }] }));
    expect(labels(estimateTokens(input)!.rows)).toEqual([
      "Global Prompt",
      "Conversation History",
    ]);
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
