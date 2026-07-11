import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

type Tone = "gray" | "blue" | "green" | "red" | "purple" | "outline";

const tones: Record<Tone, string> = {
  gray: "bg-app text-sub border border-line",
  blue: "bg-blue-50 text-accent-deep border border-blue-100",
  green: "bg-green-50 text-green-700 border border-green-100",
  red: "bg-red-50 text-red-600 border border-red-100",
  purple: "bg-purple-50 text-purple-700 border border-purple-100",
  outline: "bg-white text-ink border border-line",
};

export default function Badge({
  tone = "gray",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
