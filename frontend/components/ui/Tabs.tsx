"use client";

import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function PillTabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: { key: string; label: ReactNode }[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("inline-flex items-center gap-0.5 rounded-lg bg-app p-0.5 border border-line", className)}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={cn(
            "rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors cursor-pointer",
            active === t.key
              ? "bg-white text-ink shadow-sm border border-line"
              : "text-sub hover:text-ink",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function UnderlineTabs({
  tabs,
  active,
  onChange,
  className,
}: {
  tabs: { key: string; label: ReactNode }[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-5 border-b border-line", className)}>
      {tabs.map((t) => (
        <button
          key={t.key}
          onClick={() => onChange(t.key)}
          className={cn(
            "-mb-px flex items-center gap-1.5 border-b-2 pb-2.5 text-[13px] font-medium transition-colors cursor-pointer",
            active === t.key
              ? "border-ink text-ink"
              : "border-transparent text-sub hover:text-ink",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
