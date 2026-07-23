"use client";

import type { TranscriptItem } from "@/lib/types";
import { ChevronDown, ChevronRight, Library } from "lucide-react";
import { useState } from "react";

/** Pretty-print JSON payloads; non-JSON content renders verbatim. */
function prettyJson(s: string): string {
  try {
    return JSON.stringify(JSON.parse(s), null, 2);
  } catch {
    return s;
  }
}

/** Retell-style collapsible block for a tool invocation or result. */
function ToolBlock({ item }: { item: TranscriptItem }) {
  const [open, setOpen] = useState(true);
  const title =
    item.role === "tool_invocation"
      ? `Tool Invocation${item.name ? `: ${item.name}` : ""}`
      : "Tool Result";
  return (
    <div>
      <button
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 text-[13px] font-medium text-accent-deep cursor-pointer"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        {title}
        <span className="ml-auto text-[11px] font-normal text-faint">{item.time}</span>
      </button>
      {open && (
        <div className="mt-1.5 rounded-lg border border-line bg-app/50 px-3 py-2 font-mono text-[12px] leading-relaxed">
          {item.tool_call_id && (
            <div className="mb-1 text-sub">tool_call_id: {item.tool_call_id}</div>
          )}
          <pre className="whitespace-pre-wrap break-words">{prettyJson(item.content)}</pre>
        </div>
      )}
    </div>
  );
}

export default function Transcript({ turns }: { turns: TranscriptItem[] }) {
  if (!turns.length) {
    return <p className="py-8 text-center text-[13px] text-sub">No transcript available.</p>;
  }
  return (
    <div className="space-y-3">
      {turns.map((t, i) =>
        t.role === "tool_invocation" || t.role === "tool_result" ? (
          <ToolBlock key={i} item={t} />
        ) : t.role === "kb_retrieval" ? (
          <div key={i} className="flex items-center gap-2 py-0.5">
            <div className="h-px grow bg-line" />
            <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-app px-2.5 py-0.5 text-[11.5px] font-medium text-sub">
              <Library className="size-3" />
              Knowledge Base Retrieval
            </span>
            <div className="h-px grow bg-line" />
          </div>
        ) : (
          <div key={i} className={t.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div className="max-w-[78%]">
              <div
                className={
                  t.role === "user"
                    ? "rounded-2xl rounded-br-sm bg-ink px-3.5 py-2 text-[13px] text-white"
                    : "rounded-2xl rounded-bl-sm border border-line bg-app px-3.5 py-2 text-[13px]"
                }
              >
                {t.content}
              </div>
              <div
                className={`mt-0.5 text-[11px] text-faint ${t.role === "user" ? "text-right" : ""}`}
              >
                {t.role === "user" ? "User" : "Agent"}
                {t.time ? ` · ${t.time}` : ""}
              </div>
            </div>
          </div>
        ),
      )}
    </div>
  );
}
