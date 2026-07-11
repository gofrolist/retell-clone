"use client";

import { PillTabs } from "@/components/ui/Tabs";
import { Braces, Info, Mic, Play } from "lucide-react";
import { useState } from "react";

export default function TestPanel() {
  const [tab, setTab] = useState("audio");
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between px-4 pt-3">
        <PillTabs
          tabs={[
            { key: "audio", label: "Test Audio" },
            { key: "llm", label: "Test LLM" },
          ]}
          active={tab}
          onChange={setTab}
        />
        <button
          disabled
          title="Not available yet"
          className="flex size-8 items-center justify-center rounded-lg border border-line bg-white text-sub opacity-40 cursor-not-allowed"
          aria-label="Dynamic variables"
        >
          <Braces className="size-4" />
        </button>
      </div>

      <div className="flex grow flex-col items-center justify-center gap-6 px-6">
        <div className="relative flex size-24 items-center justify-center">
          <span className="absolute inset-0 rounded-full bg-app" />
          <span className="absolute inset-3 rounded-full border border-line bg-white shadow-sm" />
          <Mic className="relative size-8 text-line-strong" strokeWidth={1.5} />
        </div>
      </div>

      <div className="space-y-3 px-6 pb-6">
        <p className="flex items-center justify-center gap-1.5 text-center text-xs text-sub">
          <Info className="size-3.5 shrink-0" />
          Please note call transfer is not supported in Webcall.
        </p>
        <div className="flex justify-center">
          <button
            disabled
            title="Test calls not available yet"
            className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-5 text-[13px] font-medium shadow-sm opacity-50 cursor-not-allowed"
          >
            <Play className="size-3.5" />
            Run Test
          </button>
        </div>
      </div>
    </div>
  );
}
