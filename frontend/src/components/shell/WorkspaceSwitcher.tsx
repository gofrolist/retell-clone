"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

// Single-workspace product: this shows the real workspace name, no switching.
export default function WorkspaceSwitcher() {
  const [name, setName] = useState("Arhiteq Workspace");

  useEffect(() => {
    api
      .getWorkspace()
      .then((ws) => ws.name && setName(ws.name))
      .catch(() => {}); // keep the fallback; the banner covers backend-down
  }, []);

  return (
    <div className="flex w-full items-center gap-2 rounded-lg border border-line bg-white px-2.5 py-2 shadow-sm cursor-default">
      <span className="flex size-5.5 items-center justify-center rounded-md bg-accent-deep text-[11px] font-semibold text-white shrink-0">
        {name.charAt(0).toUpperCase()}
      </span>
      <span className="grow truncate text-left text-[13px] font-medium">{name}</span>
    </div>
  );
}
