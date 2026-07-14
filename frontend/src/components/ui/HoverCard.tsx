"use client";

import { cn } from "@/lib/utils";
import { useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

// Keep in sync with the panel's w-72 class; used to clamp it on-screen.
const PANEL_WIDTH = 288;
const PANEL_GAP = 6;
const VIEWPORT_MARGIN = 8;

/**
 * Hover/focus/tap popover for read-only breakdown content. The panel renders
 * into document.body with position:fixed so an overflow ancestor can never
 * clip it (or grow scrollbars from its hidden box), and it is
 * pointer-events-none so clicks pass through to controls beneath. Opens on
 * hover, keyboard focus, or tap (touch devices have no hover); closes on
 * mouse leave, blur, or Escape. className lands on the panel card.
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
  const anchorRef = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  const open = () => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left = Math.min(
      Math.max(rect.left + rect.width / 2 - PANEL_WIDTH / 2, VIEWPORT_MARGIN),
      window.innerWidth - PANEL_WIDTH - VIEWPORT_MARGIN,
    );
    setPos({ top: rect.bottom + PANEL_GAP, left });
  };
  const close = () => setPos(null);

  return (
    <span
      ref={anchorRef}
      tabIndex={0}
      aria-describedby={pos ? panelId : undefined}
      className="rounded outline-none focus-visible:ring-2 focus-visible:ring-accent-deep/40"
      onMouseEnter={open}
      onMouseLeave={close}
      onFocus={open}
      onBlur={close}
      onClick={open}
      onKeyDown={(e) => e.key === "Escape" && close()}
    >
      {trigger}
      {pos !== null &&
        createPortal(
          <div
            role="tooltip"
            id={panelId}
            style={{ top: pos.top, left: pos.left }}
            className={cn(
              "pointer-events-none fixed z-50 w-72 rounded-xl border border-line bg-card p-2 shadow-lg",
              className,
            )}
          >
            {children}
          </div>,
          document.body,
        )}
    </span>
  );
}
