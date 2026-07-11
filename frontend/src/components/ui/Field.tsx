import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

export function Field({
  label,
  hint,
  children,
  className,
  right,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
  className?: string;
  right?: ReactNode;
}) {
  return (
    <div className={className}>
      <div className="mb-1.5 flex items-center justify-between">
        <label className="text-[13px] font-medium text-ink">{label}</label>
        {right}
      </div>
      {hint && <p className="mb-1.5 -mt-0.5 text-xs text-sub">{hint}</p>}
      {children}
    </div>
  );
}

export function TextInput({
  className,
  ...props
}: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        "h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15",
        className,
      )}
      {...props}
    />
  );
}
