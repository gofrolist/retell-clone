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
  placement = "bottom",
}: {
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
  /**
   * Which side of the trigger the panel opens on. "top" is for triggers that
   * sit near the bottom of the viewport (the flow editor's agent-details card
   * is pinned there), where a downward panel would open off-screen. It is a
   * prop rather than a measurement because the panel's height is only known
   * after it renders, and `-translate-y-full` gets the same result with no
   * measure/reposition round trip.
   */
  placement?: "bottom" | "top";
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
    setPos({
      top: placement === "top" ? rect.top - PANEL_GAP : rect.bottom + PANEL_GAP,
      left,
    });
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
              placement === "top" && "-translate-y-full",
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
