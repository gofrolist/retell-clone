"use client";

import type { TranscriptItem } from "@/lib/types";
import { Library } from "lucide-react";

export default function Transcript({ turns }: { turns: TranscriptItem[] }) {
  if (!turns.length) {
    return <p className="py-8 text-center text-[13px] text-sub">No transcript available.</p>;
  }
  return (
    <div className="space-y-3">
      {turns.map((t, i) =>
        t.role === "kb_retrieval" ? (
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
