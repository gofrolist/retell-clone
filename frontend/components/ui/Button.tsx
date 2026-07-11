import { cn } from "@/lib/utils";
import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";

const styles: Record<Variant, string> = {
  primary:
    "bg-ink text-white hover:bg-black/80 border border-transparent shadow-sm",
  secondary:
    "bg-white text-ink border border-line hover:bg-app shadow-sm",
  ghost: "bg-transparent text-sub hover:bg-black/5 border border-transparent",
  danger: "bg-white text-bad border border-line hover:bg-red-50 shadow-sm",
};

export default function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
}) {
  return (
    <button
      className={cn(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed",
        size === "md" ? "h-9 px-3.5 text-[13px]" : "h-7.5 px-2.5 text-xs",
        styles[variant],
        className,
      )}
      {...props}
    />
  );
}
