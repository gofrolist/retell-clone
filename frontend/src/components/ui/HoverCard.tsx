"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * Pure-CSS hover popover, following Tooltip.tsx's approach: visibility is
 * driven by :hover / :focus-within on the wrapper, so it needs no JS state.
 * Unlike Tooltip it hosts arbitrary content rows, so the panel is opaque,
 * bordered, and interactive-width.
 */
export default function HoverCard({
  trigger,
  children,
  className,
}: {
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className="group/card relative inline-flex">
      <span
        tabIndex={0}
        className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent-deep/40"
      >
        {trigger}
      </span>
      <div
        role="tooltip"
        className={cn(
          "invisible absolute left-1/2 top-full z-30 mt-1.5 w-72 -translate-x-1/2 rounded-xl border border-line bg-card p-2 opacity-0 shadow-lg transition-opacity",
          "group-hover/card:visible group-hover/card:opacity-100 group-focus-within/card:visible group-focus-within/card:opacity-100",
          className,
        )}
      >
        {children}
      </div>
    </span>
  );
}
