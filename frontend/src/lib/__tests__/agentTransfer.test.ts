/// <reference types="bun" />
import { beforeEach, describe, expect, mock, test } from "bun:test";

/**
 * Duplicate/export for a conversation-flow agent.
 *
 * `copyLlm` has always existed so a duplicated single-prompt agent gets its
 * own LLM row rather than editing the original's prompt. Flow-backed agents
 * had no equivalent: the copy inherited the original's `conversation_flow_id`,
 * so both agents pointed at one `ConversationFlow` row — dragging a node in
 * the copy rewrote the graph the original runs, and forked its published
 * version through `agents_using_flow`.
 */

const calls: { name: string; body: unknown }[] = [];

const FLOW = {
  conversation_flow_id: "cf_source",
  version: 3,
  is_published: true,
  global_prompt: "Be brief.",
  nodes: [{ id: "n1", type: "conversation" }],
  start_node_id: "n1",
  flex_mode: true,
};

const api = {
  createLlm: async (body: unknown) => {
    calls.push({ name: "createLlm", body });
    return { llm_id: "llm_copy" };
  },
  getConversationFlow: async (flowId: string) => {
    calls.push({ name: "getConversationFlow", body: flowId });
    return FLOW;
  },
  createConversationFlow: async (body: unknown) => {
    calls.push({ name: "createConversationFlow", body });
    return { conversation_flow_id: "cf_copy" };
  },
  createAgent: async (body: unknown) => {
    calls.push({ name: "createAgent", body });
    return { agent_id: "agent_copy", ...(body as Record<string, unknown>) };
  },
  createChatAgent: async (body: unknown) => {
    calls.push({ name: "createChatAgent", body });
    return { agent_id: "chat_copy", ...(body as Record<string, unknown>) };
  },
};

mock.module("@/lib/api", () => ({ api }));
mock.module("../api", () => ({ api }));

const { duplicateAgent, downloadAgentJson } = await import("../agentTransfer");

const flowAgent = {
  agent_id: "agent_source",
  agent_name: "Clara",
  version: 7,
  response_engine: { type: "conversation-flow", conversation_flow_id: "cf_source", version: 3 },
} as never;

const promptAgent = {
  agent_id: "agent_source",
  agent_name: "Clara",
  response_engine: { type: "retell-llm", llm_id: "llm_source" },
} as never;

const lastCall = (name: string) =>
  [...calls].reverse().find((c) => c.name === name) as { name: string; body: never };

beforeEach(() => {
  calls.length = 0;
});

describe("duplicateAgent", () => {
  test("a flow agent's copy gets its OWN conversation flow", () => {
    return duplicateAgent(flowAgent, null).then(() => {
      expect(calls.map((c) => c.name)).toEqual([
        "getConversationFlow",
        "createConversationFlow",
        "createAgent",
      ]);
      const engine = (lastCall("createAgent").body as Record<string, never>)
        .response_engine as unknown as Record<string, unknown>;
      expect(engine.conversation_flow_id).toBe("cf_copy");
      expect(engine.type).toBe("conversation-flow");
    });
  });

  test("the copied flow keeps unknown Retell keys but not server-owned ones", () => {
    return duplicateAgent(flowAgent, null).then(() => {
      const body = lastCall("createConversationFlow").body as Record<string, unknown>;
      // Fidelity: a key this app does not model rides along untouched.
      expect(body.flex_mode).toBe(true);
      expect(body.global_prompt).toBe("Be brief.");
      // Server-owned: reusing these would 409 or describe the source's history.
      expect(body.conversation_flow_id).toBeUndefined();
      expect(body.version).toBeUndefined();
      expect(body.is_published).toBeUndefined();
    });
  });

  test("a single-prompt agent still copies its LLM and never touches flows", () => {
    return duplicateAgent(promptAgent, { llm_id: "llm_source", general_prompt: "Hi" } as never).then(
      () => {
        expect(calls.map((c) => c.name)).toEqual(["createLlm", "createAgent"]);
        const engine = (lastCall("createAgent").body as Record<string, never>)
          .response_engine as unknown as Record<string, unknown>;
        expect(engine).toEqual({ type: "retell-llm", llm_id: "llm_copy" });
      },
    );
  });
});

describe("downloadAgentJson", () => {
  test("a flow agent's export inlines the graph", async () => {
    // The export used to write `{agent, llm}` with llm null for a flow agent,
    // so the file carried only a `conversation_flow_id` naming a row that
    // exists in no other workspace.
    let written = "";
    const originalBlob = globalThis.Blob;
    // @ts-expect-error - minimal stand-in for the download plumbing
    globalThis.Blob = class {
      constructor(parts: string[]) {
        written = parts.join("");
      }
    };
    const originalCreate = globalThis.document?.createElement;
    const originalUrl = globalThis.URL?.createObjectURL;
    globalThis.document = {
      createElement: () => ({ href: "", download: "", click: () => {} }),
    } as never;
    globalThis.URL = { createObjectURL: () => "blob:x", revokeObjectURL: () => {} } as never;

    try {
      await downloadAgentJson(flowAgent, null);
      const payload = JSON.parse(written);
      expect(payload.conversation_flow.conversation_flow_id).toBe("cf_source");
      expect(payload.conversation_flow.nodes).toHaveLength(1);
    } finally {
      globalThis.Blob = originalBlob;
      if (originalCreate) globalThis.document.createElement = originalCreate;
      if (originalUrl) globalThis.URL.createObjectURL = originalUrl;
    }
  });
});
