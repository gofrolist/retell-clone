"use client";

import { cn } from "@/lib/utils";

export default function Toggle({
  checked,
  onChange,
  disabled,
}: {
  checked: boolean;
  onChange?: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange?.(!checked)}
      className={cn(
        "relative h-5 w-9 rounded-full transition-colors cursor-pointer disabled:opacity-50 shrink-0",
        checked ? "bg-ink" : "bg-line-strong",
      )}
    >
      <span
        className={cn(
          "absolute top-0.5 size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4.5" : "translate-x-0.5",
        )}
      />
    </button>
  );
}
