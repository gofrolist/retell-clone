"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";
import { useId } from "react";

/**
 * Pure-CSS hover popover, following Tooltip.tsx's approach: visibility is
 * driven by :hover / :focus-within on the wrapper, so it needs no JS state.
 * Unlike Tooltip it hosts arbitrary content rows, so the panel is opaque,
 * bordered, and interactive-width.
 * The gap below the trigger is padding inside the hover box (not margin), so
 * the pointer can travel into the panel without dropping :hover; className
 * lands on the inner visual card, unlike Tooltip where it hits the wrapper.
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
  const panelId = useId();
  return (
    <span className="group/card relative inline-flex">
      <span
        tabIndex={0}
        aria-describedby={panelId}
        className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent-deep/40"
      >
        {trigger}
      </span>
      <div
        role="tooltip"
        id={panelId}
        className={cn(
          "invisible absolute left-1/2 top-full z-30 w-72 -translate-x-1/2 pt-1.5 opacity-0 transition-opacity",
          "group-hover/card:visible group-hover/card:opacity-100 group-focus-within/card:visible group-focus-within/card:opacity-100",
        )}
      >
        <div className={cn("rounded-xl border border-line bg-card p-2 shadow-lg", className)}>
          {children}
        </div>
      </div>
    </span>
  );
}
