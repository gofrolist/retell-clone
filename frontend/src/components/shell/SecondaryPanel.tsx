"use client";

import { cn } from "@/lib/utils";
import { ChevronsLeft, ChevronsRight } from "lucide-react";
import { useState, type ReactNode } from "react";

/**
 * Collapsible secondary panel (list pane) used by Agents, Knowledge Base
 * and Phone Numbers. Renders panel + content side by side with a small
 * collapse handle on the panel edge.
 */
export default function SecondaryPanel({
  panel,
  children,
  width = "w-72",
}: {
  panel: ReactNode;
  children: ReactNode;
  width?: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className="flex h-full min-w-0 grow">
      <div
        className={cn(
          "relative shrink-0 border-r border-line bg-app transition-all",
          collapsed ? "w-0 overflow-hidden border-r-0" : width,
        )}
      >
        <div className={cn("h-full overflow-y-auto", collapsed && "invisible")}>
          {panel}
        </div>
      </div>
      <div className="relative min-w-0 grow overflow-y-auto bg-card">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="absolute left-0 top-8 z-10 flex size-6 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-white text-faint shadow-sm hover:text-ink cursor-pointer"
          aria-label={collapsed ? "Expand panel" : "Collapse panel"}
        >
          {collapsed ? (
            <ChevronsRight className="size-3.5" />
          ) : (
            <ChevronsLeft className="size-3.5" />
          )}
        </button>
        {children}
      </div>
    </div>
  );
}
