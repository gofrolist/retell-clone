"use client";

import Button from "@/components/ui/Button";
import Slider from "@/components/ui/Slider";
import { useClickOutside } from "@/lib/useClickOutside";
import { cn } from "@/lib/utils";
import { Settings2 } from "lucide-react";
import { useCallback, useRef, useState } from "react";

/**
 * Retell-style model settings popover behind the gear next to the model
 * picker. Currently one knob: LLM Temperature (0..1, Retell's range).
 * Cancel / outside click / Escape discard; Save commits to the page draft
 * (persisted by the header Save, same as every other field).
 */
export default function ModelSettingsPopover({
  temperature,
  onTemperature,
  live,
  attached = false,
}: {
  temperature: number;
  onTemperature: (v: number) => void;
  /** Native-audio Live sessions ignore the knob; disclose that in place. */
  live: boolean;
  /** Render as the right segment of a grouped control (border comes from the wrapper). */
  attached?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(temperature);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  // Close on outside click, discarding the local draft.
  useClickOutside(
    rootRef,
    useCallback(() => setOpen(false), []),
  );

  function toggle() {
    if (!open) setDraft(temperature);
    setOpen((o) => !o);
  }

  function close({ refocus = true } = {}) {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  }

  function save() {
    onTemperature(draft);
    close();
  }

  return (
    // Escape is handled on the root (not the dialog): after a click the focus
    // is still on the trigger, whose keydown would never bubble into a sibling.
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(e) => {
        if (open && e.key === "Escape") {
          e.preventDefault();
          close();
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={toggle}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Model settings"
        className={cn(
          "flex size-9 items-center justify-center bg-white text-sub transition-colors hover:bg-app cursor-pointer",
          attached
            ? "rounded-r-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent"
            : "rounded-lg border border-line",
          open && "bg-app",
        )}
      >
        <Settings2 className="size-4" />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Model settings"
          className="absolute left-0 top-full z-50 mt-1.5 w-[330px] rounded-xl border border-line bg-white p-4 shadow-lg shadow-black/5"
        >
          <div className="text-[14px] font-semibold text-ink">LLM Temperature</div>
          <p className="mt-0.5 text-[12px] leading-snug text-sub">
            Lower value yields better function call results.
          </p>
          <div className="mt-3">
            <Slider
              min={0}
              max={1}
              step={0.01}
              value={draft}
              onChange={setDraft}
              format={(v) => v.toFixed(2)}
            />
          </div>
          {live && (
            <p className="mt-2 rounded-lg bg-app px-2.5 py-2 text-[12px] leading-snug text-sub">
              This agent uses a native-audio Live model, which speaks at the
              model&apos;s default temperature unless the deployment pins one —
              this setting applies only to non-Live models.
            </p>
          )}
          <div className="mt-3 flex justify-end gap-2">
            <Button size="sm" variant="secondary" onClick={() => close()}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={save}>
              Save
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
