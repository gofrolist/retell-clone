"use client";

import { useClickOutside } from "@/lib/useClickOutside";
import { MoreHorizontal } from "lucide-react";
import { useCallback, useRef, useState } from "react";

/** Row overflow menu with a single Delete action; dismisses on outside click. */
export default function RowMenu({ onDelete }: { onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useClickOutside(
    ref,
    useCallback(() => setOpen(false), []),
  );

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded-md p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
        aria-label="More"
      >
        <MoreHorizontal className="size-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-32 rounded-lg border border-line bg-white p-1 shadow-lg">
          <button
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[13px] text-bad hover:bg-app cursor-pointer"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
