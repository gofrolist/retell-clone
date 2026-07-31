// Copying an agent around: duplicate, convert to a chat agent, and the
// JSON export that /agents' Import reads back. Shared by the agents-list row
// menu and the editor header so the two stay in step.

import { api, type RawAgent, type RawChatAgent, type RawLlm } from "./api";

/**
 * Fields the server owns. They must not ride along into a create call:
 * `agent_id` / `llm_id` / `conversation_flow_id` would 409 against the row
 * they were copied from, and the version bookkeeping describes the original's
 * history, not the copy's.
 */
const SERVER_OWNED = new Set([
  "agent_id",
  "llm_id",
  "conversation_flow_id",
  "version",
  "is_published",
  "published_version",
  "last_modification_timestamp",
  "created_timestamp",
  "agent_type",
]);

/** The copyable half of an agent/LLM: everything the server doesn't assign. */
function config(source: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(source).filter(([key, value]) => !SERVER_OWNED.has(key) && value !== null),
  );
}

/**
 * Copy the prompt/tools onto a fresh LLM so edits to the copy don't rewrite
 * the original's prompt (both agents would otherwise point at one LLM row).
 * Returns undefined for agents that run a conversation flow instead.
 */
async function copyLlm(llm: RawLlm | null): Promise<string | undefined> {
  if (!llm) return undefined;
  const copy = await api.createLlm(config(llm) as Partial<RawLlm>);
  return copy.llm_id;
}

/**
 * Copy the graph onto a fresh conversation flow — `copyLlm`'s counterpart for
 * flow-backed agents, and needed for exactly the same reason.
 *
 * Reusing the original's `conversation_flow_id` makes both agents point at one
 * `ConversationFlow` row: open the copy in the flow editor, drag a node, and
 * the autosave `PATCH /update-conversation-flow` rewrites the graph the
 * ORIGINAL runs — and, through `agents_using_flow` → `ensure_seeded`/`touch`,
 * forks the original's published version too.
 *
 * Returns undefined for agents that have no flow (single-prompt ones).
 */
async function copyFlow(agent: RawAgent): Promise<string | undefined> {
  const flowId = agent.response_engine?.conversation_flow_id;
  if (!flowId) return undefined;
  const flow = await api.getConversationFlow(flowId);
  const copy = await api.createConversationFlow(config(flow));
  return copy.conversation_flow_id;
}

/**
 * The copy's engine: its own LLM, or its own flow, or — for an engine kind we
 * do not know how to copy (`custom-llm`, which lives on the customer's side
 * and has nothing here to duplicate) — the original's, unchanged.
 *
 * The flow branch spreads the original engine so an unmodelled key survives,
 * but drops `version`: it numbers the SOURCE flow's history, and the copy is
 * a new row starting over at 0.
 */
function responseEngine(agent: RawAgent, llmId?: string, flowId?: string) {
  if (llmId) return { type: "retell-llm", llm_id: llmId };
  if (flowId) {
    const { version: _sourceVersion, ...engine } = agent.response_engine ?? {};
    return { ...engine, type: "conversation-flow", conversation_flow_id: flowId };
  }
  return agent.response_engine;
}

/** "Duplicate": a new agent with the same config and its own LLM/flow copy. */
export async function duplicateAgent(
  agent: RawAgent,
  llm: RawLlm | null,
): Promise<RawAgent> {
  const llmId = await copyLlm(llm);
  const flowId = llmId ? undefined : await copyFlow(agent);
  return api.createAgent({
    ...config(agent),
    agent_name: `Copy of ${agent.agent_name ?? "Untitled agent"}`,
    response_engine: responseEngine(agent, llmId, flowId),
  });
}

/** "Duplicate" for a chat agent — same shape, chat-agent endpoints. */
export async function duplicateChatAgent(
  agent: RawAgent,
  llm: RawLlm | null,
): Promise<RawChatAgent> {
  const llmId = await copyLlm(llm);
  const flowId = llmId ? undefined : await copyFlow(agent);
  return api.createChatAgent({
    agent_name: `Copy of ${agent.agent_name ?? "Untitled agent"}`,
    response_engine: responseEngine(agent, llmId, flowId),
    language: agent.language,
    webhook_url: agent.webhook_url,
  });
}

/**
 * "Convert to Chat Agent": a text-only twin of a voice agent. The original is
 * left alone (Retell does the same) — only the prompt, tools and webhook
 * carry over, since a chat agent has no voice or telephony settings.
 */
export async function convertToChatAgent(
  agent: RawAgent,
  llm: RawLlm | null,
): Promise<RawChatAgent> {
  if (!llm) {
    throw new Error("Only single-prompt agents can be converted to a chat agent");
  }
  const llmId = await copyLlm(llm);
  return api.createChatAgent({
    agent_name: `${agent.agent_name ?? "Untitled agent"} (Chat)`,
    response_engine: { type: "retell-llm", llm_id: llmId },
    language: agent.language,
    webhook_url: agent.webhook_url,
  });
}

/**
 * "Export": download the agent as JSON. The `{ agent, llm, conversation_flow }`
 * shape is what /agents' Import reads, so an export can be imported back —
 * here or into another workspace.
 *
 * The flow is fetched and inlined for the same reason the LLM is: without it
 * the file carries only a `conversation_flow_id`, which names a row that does
 * not exist anywhere else. Importing that into another workspace 404s on
 * `create-agent`'s flow check, and importing it back into this one produces a
 * second agent sharing the source graph (see `copyFlow`).
 */
export async function downloadAgentJson(agent: RawAgent, llm: RawLlm | null): Promise<void> {
  const flowId = agent.response_engine?.conversation_flow_id;
  const conversationFlow = flowId ? await api.getConversationFlow(flowId) : null;
  const blob = new Blob([JSON.stringify({ agent, llm, conversation_flow: conversationFlow }, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(agent.agent_name ?? agent.agent_id).replace(/[^\w.-]+/g, "_")}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
