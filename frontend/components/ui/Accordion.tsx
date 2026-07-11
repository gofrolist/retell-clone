"use client";

import { cn } from "@/lib/utils";
import { ChevronDown, type LucideIcon } from "lucide-react";
import { useState, type ReactNode } from "react";

export default function Accordion({
  icon: Icon,
  title,
  children,
  defaultOpen = false,
}: {
  icon: LucideIcon;
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-b border-line last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2.5 px-4 py-3.5 text-left cursor-pointer hover:bg-app/60 transition-colors"
      >
        <Icon className="size-4 text-sub shrink-0" />
        <span className="grow text-[13.5px] font-medium">{title}</span>
        <ChevronDown
          className={cn("size-4 text-faint transition-transform", open && "rotate-180")}
        />
      </button>
      {open && <div className="space-y-4 px-4 pt-1 pb-5">{children}</div>}
    </div>
  );
}
