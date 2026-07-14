"use client";

import { useCopied } from "@/lib/useCopied";
import { cn } from "@/lib/utils";
import { Check, Copy } from "lucide-react";

export default function CopyId({
  value,
  display,
  className,
}: {
  value: string;
  display?: string;
  className?: string;
}) {
  const { copiedKey: copied, copy } = useCopied(1200);
  return (
    <button
      onClick={() => copy(value)}
      className={cn(
        "inline-flex items-center gap-1 text-[13px] text-sub hover:text-ink cursor-pointer",
        className,
      )}
      title={value}
    >
      <span className="font-mono text-[12.5px]">{display ?? value}</span>
      {copied ? (
        <Check className="size-3.5 text-ok" />
      ) : (
        <Copy className="size-3.5 text-faint" />
      )}
    </button>
  );
}
