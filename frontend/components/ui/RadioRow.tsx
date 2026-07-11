"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function RadioRow({
  checked,
  onSelect,
  label,
  suffix,
}: {
  checked: boolean;
  onSelect: () => void;
  label: ReactNode;
  suffix?: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="flex w-full items-center gap-2.5 py-1.5 text-left cursor-pointer"
    >
      <span
        className={cn(
          "flex size-4 items-center justify-center rounded-full border shrink-0",
          checked ? "border-ink" : "border-line-strong",
        )}
      >
        {checked && <span className="size-2 rounded-full bg-ink" />}
      </span>
      <span className="text-[13px]">{label}</span>
      {suffix}
    </button>
  );
}

export function CheckboxRow({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex items-center gap-2 py-1 text-left cursor-pointer"
    >
      <span
        className={cn(
          "flex size-4 items-center justify-center rounded border shrink-0 transition-colors",
          checked ? "border-ink bg-ink" : "border-line-strong bg-white",
        )}
      >
        {checked && (
          <svg viewBox="0 0 12 12" className="size-3 text-white" fill="none">
            <path d="M2.5 6.5L5 9l4.5-5.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      <span className="text-[13px]">{label}</span>
    </button>
  );
}
