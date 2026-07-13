"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

/**
 * Pure-CSS hover tooltip. Hover is detected on the wrapper span, so it also
 * works around disabled buttons (which swallow JS mouse events but still
 * let the parent match :hover).
 */
export default function Tooltip({
  label,
  children,
  className,
}: {
  label: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group/tooltip relative inline-flex", className)}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-full left-1/2 z-20 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-md bg-ink px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity group-hover/tooltip:opacity-100"
      >
        {label}
      </span>
    </span>
  );
}
