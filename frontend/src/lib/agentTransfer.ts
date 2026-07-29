// Copying an agent around: duplicate, convert to a chat agent, and the
// JSON export that /agents' Import reads back. Shared by the agents-list row
// menu and the editor header so the two stay in step.

import { api, type RawAgent, type RawChatAgent, type RawLlm } from "./api";

/**
 * Fields the server owns. They must not ride along into a create call:
 * `agent_id` / `llm_id` would 409 against the row they were copied from, and
 * the version bookkeeping describes the original's history, not the copy's.
 */
const SERVER_OWNED = new Set([
  "agent_id",
  "llm_id",
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

/** Conversation-flow agents have no LLM to copy; the copy runs the same flow. */
function responseEngine(agent: RawAgent, llmId: string | undefined) {
  return llmId ? { type: "retell-llm", llm_id: llmId } : agent.response_engine;
}

/** "Duplicate": a new agent with the same config and its own LLM copy. */
export async function duplicateAgent(
  agent: RawAgent,
  llm: RawLlm | null,
): Promise<RawAgent> {
  const llmId = await copyLlm(llm);
  return api.createAgent({
    ...config(agent),
    agent_name: `Copy of ${agent.agent_name ?? "Untitled agent"}`,
    response_engine: responseEngine(agent, llmId),
  });
}

/** "Duplicate" for a chat agent — same shape, chat-agent endpoints. */
export async function duplicateChatAgent(
  agent: RawAgent,
  llm: RawLlm | null,
): Promise<RawChatAgent> {
  const llmId = await copyLlm(llm);
  return api.createChatAgent({
    agent_name: `Copy of ${agent.agent_name ?? "Untitled agent"}`,
    response_engine: responseEngine(agent, llmId),
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
 * "Export": download the agent as JSON. The `{ agent, llm }` shape is what
 * /agents' Import reads, so an export can be imported back — here or into
 * another workspace.
 */
export function downloadAgentJson(agent: RawAgent, llm: RawLlm | null): void {
  const blob = new Blob([JSON.stringify({ agent, llm }, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(agent.agent_name ?? agent.agent_id).replace(/[^\w.-]+/g, "_")}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
}
