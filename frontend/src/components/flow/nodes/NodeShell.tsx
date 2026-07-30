"use client";

import { cn } from "@/lib/utils";
import { Globe, type LucideIcon } from "lucide-react";
import { Handle, Position } from "@xyflow/react";

/**
 * Presentational shell every node type renders through: a target handle on
 * top, a title bar (icon, name, optional START/global pills), a two-line
 * subtitle, and a source handle on the bottom. Fixed width so edges land at
 * predictable points regardless of node type.
 */
export default function NodeShell({
  icon: Icon,
  accent,
  title,
  subtitle,
  isStart,
  isGlobal,
  selected,
}: {
  icon: LucideIcon;
  accent: string;
  title: string;
  subtitle: string;
  isStart: boolean;
  isGlobal: boolean;
  selected: boolean;
}) {
  return (
    <div
      className={cn(
        "w-[260px] rounded-lg border border-line bg-card shadow-sm",
        selected && "ring-1 ring-ink",
      )}
    >
      <Handle type="target" position={Position.Top} className="!bg-faint" />
      <div className={cn("flex items-center gap-2 rounded-t-lg px-3 py-2", accent)}>
        <Icon className="size-3.5 shrink-0" />
        <span className="grow truncate text-[13px] font-medium">{title}</span>
        {isStart && (
          <span className="shrink-0 rounded-full bg-white/70 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide">
            START
          </span>
        )}
        {isGlobal && <Globe className="size-3.5 shrink-0" />}
      </div>
      {subtitle && (
        <div className="px-3 py-2">
          <p className="line-clamp-2 text-[12px] text-sub">{subtitle}</p>
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-faint" />
    </div>
  );
}
