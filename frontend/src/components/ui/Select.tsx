"use client";

import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import type { ReactNode } from "react";

/** Native select styled as a bordered dropdown button. */
export default function Select({
  value,
  onChange,
  options,
  className,
  prefix,
}: {
  value: string;
  onChange?: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
  prefix?: ReactNode;
}) {
  return (
    <div className={cn("relative inline-flex items-center", className)}>
      {prefix && (
        <span className="pointer-events-none absolute left-2.5 z-10 flex items-center">
          {prefix}
        </span>
      )}
      <select
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        className={cn(
          "h-9 w-full cursor-pointer appearance-none rounded-lg border border-line bg-white pr-8 text-[13px] font-medium outline-none transition-colors hover:bg-app focus:border-accent",
          prefix ? "pl-8" : "pl-3",
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-2.5 size-3.5 text-faint" />
    </div>
  );
}
