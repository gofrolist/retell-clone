import { cn } from "@/lib/utils";

const colors: Record<string, string> = {
  green: "bg-ok",
  red: "bg-bad",
  blue: "bg-accent",
  gray: "bg-faint",
  orange: "bg-orange-400",
};

export default function StatusDot({
  color,
  label,
  className,
}: {
  color: "green" | "red" | "blue" | "gray" | "orange";
  label?: string;
  className?: string;
}) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className={cn("size-1.5 rounded-full shrink-0", colors[color])} />
      {label && <span className="text-[13px]">{label}</span>}
    </span>
  );
}
