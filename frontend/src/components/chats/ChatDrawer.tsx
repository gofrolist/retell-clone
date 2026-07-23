"use client";

import CopyId from "@/components/ui/CopyId";
import StatusDot from "@/components/ui/StatusDot";
import { api, type ChatMessage, type RawChat } from "@/lib/api";
import { cn, formatCallTime, formatDurationLong, truncateId } from "@/lib/utils";
import { ChevronDown, ChevronUp, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 py-1 text-[13px]">
      <span className="w-28 shrink-0 text-sub">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

function MessageBubble({ m }: { m: ChatMessage }) {
  const isAgent = m.role === "agent";
  return (
    <div className={cn("flex", isAgent ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[75%] rounded-xl px-3 py-2 text-[13px] leading-relaxed",
          isAgent ? "bg-app text-ink" : "bg-ink text-white",
        )}
      >
        <div className={cn("mb-0.5 text-[11px] font-medium", isAgent ? "text-sub" : "text-white/70")}>
          {isAgent ? "Agent" : "User"}
          {m.created_timestamp ? ` · ${formatCallTime(m.created_timestamp).split("·")[1] ?? ""}` : ""}
        </div>
        <div className="whitespace-pre-wrap break-words">{m.content}</div>
      </div>
    </div>
  );
}

export default function ChatDrawer({
  chat,
  agentNames,
  onClose,
  onNavigate,
}: {
  chat: RawChat;
  agentNames: Record<string, string>;
  onClose: () => void;
  onNavigate: (dir: 1 | -1) => void;
}) {
  // list rows can be partial — fetch the full chat on open
  const [full, setFull] = useState<RawChat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    drawerRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setFull(null);
    setError(null);
    api
      .getChat(chat.chat_id)
      .then((c) => {
        if (!cancelled) setFull(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load chat");
      });
    return () => {
      cancelled = true;
    };
  }, [chat.chat_id]);

  const c = full ?? chat;
  const messages = c.message_with_tool_calls ?? [];
  const started = c.start_timestamp ?? 0;
  const duration = c.end_timestamp && started ? c.end_timestamp - started : 0;
  const dynamicVars = Object.entries(c.retell_llm_dynamic_variables ?? {});

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/25"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Chat details"
        tabIndex={-1}
        className="flex h-full w-full max-w-3xl flex-col bg-card shadow-2xl outline-none"
      >
        <div className="flex items-center gap-2 border-b border-line px-5 py-3">
          <button
            onClick={onClose}
            className="rounded-md p-1 text-sub hover:bg-app cursor-pointer"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
          <span className="text-[13px] text-sub">
            <kbd className="rounded border border-line bg-app px-1">↑</kbd>{" "}
            <kbd className="rounded border border-line bg-app px-1">↓</kbd> to navigate
          </span>
          <span className="ml-auto flex items-center gap-1">
            {!full && !error && <span className="text-[12px] text-faint">Loading…</span>}
            <button
              onClick={() => onNavigate(-1)}
              className="rounded-md p-1 text-sub hover:bg-app cursor-pointer"
              aria-label="Previous chat"
            >
              <ChevronUp className="size-4" />
            </button>
            <button
              onClick={() => onNavigate(1)}
              className="rounded-md p-1 text-sub hover:bg-app cursor-pointer"
              aria-label="Next chat"
            >
              <ChevronDown className="size-4" />
            </button>
          </span>
        </div>

        <div className="min-h-0 grow overflow-y-auto px-5 py-4">
          <h2 className="text-[15px] font-semibold">{formatCallTime(started)}</h2>

          {error && (
            <p className="mt-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
              {error}
            </p>
          )}

          <div className="mt-3">
            <MetaItem label="Agent">
              <span className="flex items-center gap-1.5">
                {agentNames[c.agent_id] ?? c.agent_id}
                <CopyId value={c.agent_id} display={truncateId(c.agent_id, 14)} />
              </span>
            </MetaItem>
            <MetaItem label="Version">{c.agent_version ?? 0}</MetaItem>
            <MetaItem label="Chat ID">
              <CopyId value={c.chat_id} display={truncateId(c.chat_id, 26)} />
            </MetaItem>
            <MetaItem label="Status">
              <StatusDot
                color={c.chat_status === "ongoing" ? "blue" : "gray"}
                label={c.chat_status}
              />
            </MetaItem>
            <MetaItem label="Duration">
              {duration ? formatDurationLong(duration) : "-"}
            </MetaItem>
            <MetaItem label="Messages">
              <span className="tabular-nums">{messages.length}</span>
            </MetaItem>
          </div>

          {dynamicVars.length > 0 && (
            <section className="mt-5">
              <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-sub">
                <span className="font-mono text-faint">{"{ }"}</span> Dynamic Variables
              </h3>
              <dl className="rounded-lg border border-line bg-app/50 divide-y divide-line/70">
                {dynamicVars.map(([k, v]) => (
                  <div key={k} className="px-3 py-2 text-[13px]">
                    <dt className="font-mono text-[12px] text-sub">{k}</dt>
                    <dd className="mt-0.5 break-words whitespace-pre-wrap">{v}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          <section className="mt-5 pb-6">
            <h3 className="mb-3 text-[14px] font-semibold">Conversation</h3>
            {messages.length === 0 ? (
              <p className="py-8 text-center text-[13px] text-sub">No messages in this chat.</p>
            ) : (
              <div className="space-y-2.5">
                {messages.map((m) => (
                  <MessageBubble key={m.message_id} m={m} />
                ))}
              </div>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
