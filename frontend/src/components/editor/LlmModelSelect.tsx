"use client";

import { formatUsdPerMin, llmDisplayCostPerMin } from "@/lib/estimates";
import { isLiveModel, LLM_MODELS, type LlmModel } from "@/lib/models";
import { cn } from "@/lib/utils";
import { ChevronDown, ChevronUp, Radio } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

/** The Gemini "spark" mark with its blue→purple→pink gradient. */
function GeminiMark({ className }: { className?: string }) {
  // Per-instance gradient id: this mark renders many times (trigger + every
  // option), and a shared literal id is invalid and can drop the fill to black
  // when the first-defining instance unmounts (Safari/Firefox).
  const gradId = useId();
  return (
    <svg viewBox="0 0 16 16" aria-hidden className={className}>
      <defs>
        <linearGradient id={gradId} x1="0" y1="0" x2="16" y2="16" gradientUnits="userSpaceOnUse">
          <stop offset="0" stopColor="#4285F4" />
          <stop offset="0.5" stopColor="#9B72CB" />
          <stop offset="1" stopColor="#D96570" />
        </linearGradient>
      </defs>
      <path
        d="M8 0c0 4.42-3.58 8-8 8 4.42 0 8 3.58 8 8 0-4.42 3.58-8 8-8-4.42 0-8-3.58-8-8Z"
        fill={`url(#${gradId})`}
      />
    </svg>
  );
}

/** Small monochrome pill (e.g. "Suggested", "Live"). */
function Pill({ children, tone = "muted" }: { children: React.ReactNode; tone?: "muted" | "live" }) {
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-0.5 text-[11px] font-medium leading-none",
        tone === "live" ? "bg-purple-100 text-purple-700" : "bg-app text-sub",
      )}
    >
      {children}
    </span>
  );
}

/**
 * Retell-style LLM picker: a custom popover (not a native <select>) so each
 * model can show a provider mark, a "Suggested" pill, and a per-minute cost.
 * Keyboard: Enter/Space or ↓ opens; ↑/↓ move; Enter selects; Esc closes.
 */
export default function LlmModelSelect({
  value,
  onChange,
  className,
  attached = false,
}: {
  value: string;
  onChange?: (v: string) => void;
  className?: string;
  /** Render as the left segment of a grouped control (border comes from the wrapper). */
  attached?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);

  // Catalog plus, if the stored id isn't a preset (e.g. an imported model),
  // a synthetic entry so the current value is always selectable.
  const options = useMemo<LlmModel[]>(() => {
    const inCatalog = LLM_MODELS.some((m) => m.id === value);
    if (inCatalog || !value) return [...LLM_MODELS];
    return [{ id: value, label: value, provider: "google", live: isLiveModel(value) }, ...LLM_MODELS];
  }, [value]);

  // Undefined when nothing is stored yet, so the trigger shows a placeholder
  // instead of pretending the first catalog model is selected.
  const selected = value ? options.find((m) => m.id === value) : undefined;
  const selectedLive = isLiveModel(value);

  // Return focus to the trigger whenever the popover closes via keyboard or a
  // pick, so a keyboard user doesn't get dropped back to <body>.
  function close({ refocus = true } = {}) {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // On open, point the highlight at the current value and focus it.
  useEffect(() => {
    if (!open) return;
    const idx = Math.max(0, options.findIndex((m) => m.id === value));
    setActive(idx);
    // Focus after the popover has painted.
    const id = requestAnimationFrame(() => optionRefs.current[idx]?.focus());
    return () => cancelAnimationFrame(id);
  }, [open, options, value]);

  function pick(id: string) {
    onChange?.(id);
    close();
  }

  function onListKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const dir = e.key === "ArrowDown" ? 1 : -1;
      const next = (active + dir + options.length) % options.length;
      setActive(next);
      optionRefs.current[next]?.focus();
    }
  }

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          // Enter/Space activate the button natively (-> onClick toggles); only
          // ArrowDown needs handling here, and only to suppress page scroll.
          if (!open && e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={cn(
          "inline-flex h-9 w-full items-center gap-2 bg-white pl-2.5 pr-2 text-[13px] font-medium outline-none transition-colors hover:bg-app",
          attached ? "rounded-l-lg focus:bg-app" : "rounded-lg border border-line focus:border-accent",
        )}
      >
        {selectedLive ? (
          <Radio className="size-3.5 shrink-0 text-purple-600" />
        ) : (
          <GeminiMark className="size-3.5 shrink-0" />
        )}
        <span className={cn("truncate", !selected && "text-faint")}>
          {selected?.label ?? "Select a model"}
        </span>
        {open ? (
          <ChevronUp className="ml-auto size-3.5 shrink-0 text-faint" />
        ) : (
          <ChevronDown className="ml-auto size-3.5 shrink-0 text-faint" />
        )}
      </button>

      {open && (
        <div
          role="listbox"
          aria-label="Language model"
          onKeyDown={onListKeyDown}
          className="absolute left-0 top-full z-50 mt-1.5 min-w-[300px] overflow-hidden rounded-xl border border-line bg-white p-1 shadow-lg shadow-black/5"
        >
          <div className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-faint">
            Versatile and highly intelligent
          </div>
          {options.map((m, i) => {
            const isSelected = m.id === value;
            const live = m.live ?? isLiveModel(m.id);
            return (
              <button
                key={m.id}
                type="button"
                role="option"
                aria-selected={isSelected}
                tabIndex={-1}
                ref={(el) => {
                  optionRefs.current[i] = el;
                }}
                onClick={() => pick(m.id)}
                onMouseEnter={() => setActive(i)}
                className={cn(
                  "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] outline-none transition-colors",
                  isSelected ? "bg-accent/10" : active === i ? "bg-app" : "bg-transparent",
                )}
              >
                {live ? (
                  <Radio className="size-4 shrink-0 text-purple-600" />
                ) : (
                  <GeminiMark className="size-4 shrink-0" />
                )}
                <span className={cn("font-medium", isSelected ? "text-accent-deep" : "text-ink")}>
                  {m.label}
                </span>
                {m.suggested && <Pill>Suggested</Pill>}
                {live && <Pill tone="live">Live</Pill>}
                <span className="ml-auto pl-2 text-[12px] tabular-nums text-faint">
                  {formatUsdPerMin(llmDisplayCostPerMin(m.id))}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
