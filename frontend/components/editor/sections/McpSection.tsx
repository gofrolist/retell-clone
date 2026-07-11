"use client";

import { Plus } from "lucide-react";

export default function McpSection() {
  return (
    <div>
      <p className="text-[13px] text-sub">
        Connect MCP servers to give your agent access to external tools.
      </p>
      <button
        disabled
        title="Not available yet"
        className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep opacity-40 cursor-not-allowed"
      >
        <Plus className="size-3.5" /> Add MCP Server
      </button>
    </div>
  );
}
