"use client";

import { cn } from "@/lib/utils";
import { ChevronLeft, ChevronRight } from "lucide-react";

export default function Pagination({
  page,
  totalPages,
  onPage,
  summary,
  pageSizeControl,
}: {
  page: number;
  totalPages: number;
  onPage: (p: number) => void;
  summary?: string;
  pageSizeControl?: React.ReactNode;
}) {
  const pages: (number | "…")[] = [];
  if (totalPages <= 6) {
    for (let i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    pages.push(1, 2, 3, "…", totalPages);
  }
  return (
    <div className="flex items-center justify-between px-1 py-3">
      <span className="text-[13px] text-sub">{summary}</span>
      <div className="flex items-center gap-1">
        <button
          disabled={page <= 1}
          onClick={() => onPage(page - 1)}
          className="rounded-md p-1.5 text-sub hover:bg-app disabled:opacity-40 cursor-pointer"
          aria-label="Previous page"
        >
          <ChevronLeft className="size-4" />
        </button>
        {pages.map((p, i) =>
          p === "…" ? (
            <span key={`e${i}`} className="px-1.5 text-sub">
              …
            </span>
          ) : (
            <button
              key={p}
              onClick={() => onPage(p)}
              className={cn(
                "min-w-7 rounded-md px-2 py-1 text-[13px] cursor-pointer",
                p === page
                  ? "bg-white border border-line shadow-sm font-medium"
                  : "text-sub hover:bg-app",
              )}
            >
              {p}
            </button>
          ),
        )}
        <button
          disabled={page >= totalPages}
          onClick={() => onPage(page + 1)}
          className="rounded-md p-1.5 text-sub hover:bg-app disabled:opacity-40 cursor-pointer"
          aria-label="Next page"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
      <div>{pageSizeControl}</div>
    </div>
  );
}
