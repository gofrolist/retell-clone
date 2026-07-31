"use client";

import Badge from "@/components/ui/Badge";
import { api, uiAgentFromRaw, uiAgentFromRawChat } from "@/lib/api";
import {
  convertToChatAgent,
  downloadAgentJson,
  duplicateAgent,
  duplicateChatAgent,
} from "@/lib/agentTransfer";
import { cn } from "@/lib/utils";
import type { Agent, AgentFolder } from "@/lib/types";
import {
  Bot,
  Check,
  Copy,
  CopyPlus,
  Folder,
  FolderMinus,
  MessageSquare,
  MoreVertical,
  Share,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";

const AGENT_TYPE_LABEL: Record<Agent["agent_type"], string> = {
  "single-prompt": "Single Prompt",
  "conversation-flow": "Conversation Flow",
  chat: "Chat",
};

const AVATAR_COLORS = [
  "bg-emerald-100 text-emerald-700",
  "bg-sky-100 text-sky-700",
  "bg-amber-100 text-amber-700",
  "bg-rose-100 text-rose-700",
  "bg-violet-100 text-violet-700",
];

export function VoiceAvatar({ name, index }: { name: string; index: number }) {
  return (
    <span
      className={cn(
        "flex size-6 items-center justify-center rounded-full text-[11px] font-semibold shrink-0",
        AVATAR_COLORS[index % AVATAR_COLORS.length],
      )}
    >
      {name.charAt(0)}
    </span>
  );
}

export default function AgentsTable({
  agents,
  folders = [],
  onDeleted,
  onMoved,
  onCreated,
}: {
  agents: Agent[];
  folders?: AgentFolder[];
  onDeleted?: (agentId: string) => void;
  onMoved?: (agentId: string, folderId: string | null) => void;
  /** A row action minted a new agent (Duplicate / Convert to Chat Agent). */
  onCreated?: (agent: Agent) => void;
}) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  // The row is mid-action (duplicating, converting, deleting) and frozen.
  const [busyId, setBusyId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const moveToFolder = async (agent: Agent, folderId: string | null) => {
    setMenuFor(null);
    if ((agent.folder_id ?? null) === folderId) return;
    setActionError(null);
    try {
      await api.moveAgentToFolder(agent.agent_id, folderId);
      onMoved?.(agent.agent_id, folderId);
    } catch (e) {
      setActionError(
        `Failed to move "${agent.agent_name}": ${e instanceof Error ? e.message : "unknown error"}`,
      );
    }
  };

  const copyAgentId = (agentId: string) => {
    navigator.clipboard?.writeText(agentId).catch(() => {});
    setCopiedId(agentId);
    setTimeout(() => setCopiedId((prev) => (prev === agentId ? null : prev)), 1200);
  };

  /** Run a row action that needs the agent's full config (and its LLM). */
  const withDetail = async (
    agent: Agent,
    verb: string,
    run: (detail: Awaited<ReturnType<typeof api.getAgentDetail>>) => Promise<void>,
  ) => {
    setMenuFor(null);
    setBusyId(agent.agent_id);
    setActionError(null);
    try {
      await run(await api.getAgentDetail(agent.agent_id));
    } catch (e) {
      setActionError(
        `Failed to ${verb} "${agent.agent_name}": ${e instanceof Error ? e.message : "unknown error"}`,
      );
    } finally {
      setBusyId(null);
    }
  };

  const duplicate = (agent: Agent) =>
    withDetail(agent, "duplicate", async ({ agent: raw, llm, is_chat }) => {
      onCreated?.(
        is_chat
          ? uiAgentFromRawChat(await duplicateChatAgent(raw, llm))
          : uiAgentFromRaw(await duplicateAgent(raw, llm)),
      );
    });

  const exportAgent = (agent: Agent) =>
    withDetail(agent, "export", async ({ agent: raw, llm }) => {
      await downloadAgentJson(raw, llm);
    });

  const convertToChat = (agent: Agent) =>
    withDetail(agent, "convert", async ({ agent: raw, llm }) => {
      onCreated?.(uiAgentFromRawChat(await convertToChatAgent(raw, llm)));
    });

  const deleteAgent = async (agent: Agent) => {
    setMenuFor(null);
    if (!window.confirm(`Delete "${agent.agent_name}"? This cannot be undone.`)) return;
    setBusyId(agent.agent_id);
    setActionError(null);
    try {
      if (agent.agent_type === "chat") await api.deleteChatAgent(agent.agent_id);
      else await api.deleteAgent(agent.agent_id);
      onDeleted?.(agent.agent_id);
    } catch (e) {
      setActionError(
        `Failed to delete "${agent.agent_name}": ${e instanceof Error ? e.message : "unknown error"}`,
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div>
      {actionError && (
        <div className="mb-2 flex items-center justify-between gap-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[12.5px] text-red-600">
          <span>{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            className="font-medium hover:underline cursor-pointer"
          >
            Dismiss
          </button>
        </div>
      )}
      <table className="w-full text-left">
        <thead>
          <tr className="border-b border-line text-[13px] text-sub">
            <th className="py-2.5 pl-4 pr-3 font-medium">Agent Name</th>
            <th className="px-3 py-2.5 font-medium">Agent Type</th>
            <th className="px-3 py-2.5 font-medium">Voice</th>
            <th className="px-3 py-2.5 font-medium">Phone</th>
            <th className="px-3 py-2.5 font-medium">Edited by</th>
            <th className="w-10 px-3 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {agents.map((a, i) => (
            <tr
              key={a.agent_id}
              className={cn(
                "border-b border-line/70 transition-colors hover:bg-app/70",
                busyId === a.agent_id && "pointer-events-none opacity-50",
              )}
            >
              <td className="py-3 pl-4 pr-3">
                <Link
                  href={`/agents/${a.agent_id}`}
                  className="flex items-center gap-2.5 font-medium"
                >
                  <span className="flex size-7 items-center justify-center rounded-lg border border-line bg-white text-emerald-600 shadow-sm shrink-0">
                    <Bot className="size-4" strokeWidth={1.8} />
                  </span>
                  <span className="truncate max-w-56">{a.agent_name}</span>
                </Link>
              </td>
              <td className="px-3 py-3">
                <Badge tone="gray">{AGENT_TYPE_LABEL[a.agent_type]}</Badge>
              </td>
              <td className="px-3 py-3">
                {a.agent_type === "chat" ? (
                  <span className="text-sub">-</span>
                ) : (
                  <span className="flex items-center gap-2">
                    <VoiceAvatar name={a.voice_name} index={i} />
                    <span className="truncate max-w-40">{a.voice_name}</span>
                  </span>
                )}
              </td>
              <td className="px-3 py-3">
                {a.phone_number ? (
                  <Badge tone="outline">{a.phone_number}</Badge>
                ) : (
                  <span className="text-sub">-</span>
                )}
              </td>
              <td className="px-3 py-3 text-sub whitespace-nowrap">{a.edited_by ?? "-"}</td>
              <td className="relative px-3 py-3">
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuFor(menuFor === a.agent_id ? null : a.agent_id);
                  }}
                  className="rounded-md p-1 text-faint hover:bg-black/5 hover:text-ink cursor-pointer"
                  aria-label="Row actions"
                  aria-expanded={menuFor === a.agent_id}
                >
                  <MoreVertical className="size-4" />
                </button>
                {menuFor === a.agent_id && (
                  <>
                    <div
                      className="fixed inset-0 z-10"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuFor(null);
                      }}
                    />
                    <div
                      className="absolute right-3 top-10 z-20 w-56 rounded-lg border border-line bg-white py-1 shadow-lg"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <button
                        onClick={() => copyAgentId(a.agent_id)}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                      >
                        <Copy className="size-3.5 text-sub" />
                        {copiedId === a.agent_id ? "Copied" : "Copy agent ID"}
                      </button>
                      <button
                        onClick={() => void duplicate(a)}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                      >
                        <CopyPlus className="size-3.5 text-sub" />
                        Duplicate
                      </button>
                      <button
                        onClick={() => void exportAgent(a)}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                      >
                        <Share className="size-3.5 text-sub" />
                        Export
                      </button>
                      {/* A chat agent is already text-only, and a
                          conversation flow has no prompt to carry over. */}
                      {a.agent_type === "single-prompt" && (
                        <button
                          onClick={() => void convertToChat(a)}
                          className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                        >
                          <MessageSquare className="size-3.5 text-sub" />
                          Convert to Chat Agent
                        </button>
                      )}
                      {/* Folders are a voice-agent grouping; the chat-agent API
                          has no folder_id. */}
                      {folders.length > 0 && a.agent_type !== "chat" && (
                        <>
                          <div className="my-1 border-t border-line" />
                          <div className="px-3 pb-1 pt-1.5 text-[11px] font-semibold tracking-wider text-faint">
                            MOVE TO FOLDER
                          </div>
                          {folders.map((f) => (
                            <button
                              key={f.folder_id}
                              onClick={() => moveToFolder(a, f.folder_id)}
                              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                            >
                              <Folder className="size-3.5 shrink-0 text-sub" />
                              <span className="truncate">{f.folder_name}</span>
                              {a.folder_id === f.folder_id && (
                                <Check className="ml-auto size-3.5 shrink-0 text-sub" />
                              )}
                            </button>
                          ))}
                          {a.folder_id && (
                            <button
                              onClick={() => moveToFolder(a, null)}
                              className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                            >
                              <FolderMinus className="size-3.5 text-sub" />
                              Remove from folder
                            </button>
                          )}
                          <div className="my-1 border-t border-line" />
                        </>
                      )}
                      <button
                        onClick={() => deleteAgent(a)}
                        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[13px] text-bad hover:bg-red-50 cursor-pointer"
                      >
                        <Trash2 className="size-3.5" />
                        Delete
                      </button>
                    </div>
                  </>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
