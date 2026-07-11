import type { ReactNode } from "react";

export default function ChartCard({
  title,
  children,
  legend,
}: {
  title: string;
  children: ReactNode;
  legend?: ReactNode;
}) {
  return (
    <div className="flex flex-col rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="mb-3 text-[13.5px] font-semibold">{title}</div>
      <div className="min-h-0 grow">{children}</div>
      {legend && <div className="mt-2">{legend}</div>}
    </div>
  );
}

export function LegendItem({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-sub">
      <span className="size-2.5 rounded-[3px]" style={{ background: color }} />
      {label}
    </span>
  );
}
