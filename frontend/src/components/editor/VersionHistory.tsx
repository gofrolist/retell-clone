"use client";

import { api, type RawAgent } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useEffect, useState, type ReactNode } from "react";

/**
 * Trigger + popover listing an agent's versions from get-agent-versions.
 * The backend keeps a single live version per agent today, so the list is
 * usually one row — rendered truthfully, not padded into fake history.
 */
export default function VersionHistory({
  agentId,
  trigger,
  align = "right",
}: {
  agentId: string;
  trigger: (open: () => void) => ReactNode;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const [versions, setVersions] = useState<RawAgent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setVersions(null);
    setError(null);
    api
      .getAgentVersions(agentId)
      .then((v) => !cancelled && setVersions(v))
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load versions");
      });
    return () => {
      cancelled = true;
    };
  }, [open, agentId]);

  return (
    <div className="relative">
      {trigger(() => setOpen((v) => !v))}
      {open && (
        <>
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div
            className={`absolute top-full z-30 mt-1 w-72 rounded-xl border border-line bg-white p-2 shadow-lg ${align === "right" ? "right-0" : "left-0"}`}
          >
            <div className="mb-1 px-1.5 text-xs font-semibold uppercase tracking-wide text-faint">
              Version history
            </div>
            {error ? (
              <p className="px-1.5 py-2 text-[13px] text-bad">{error}</p>
            ) : versions === null ? (
              <p className="px-1.5 py-2 text-[13px] text-sub">Loading…</p>
            ) : (
              versions.map((v) => (
                <div
                  key={v.version}
                  className="flex items-center justify-between gap-2 rounded-lg px-1.5 py-2 hover:bg-app"
                >
                  <span className="text-[13px] font-medium">V{v.version}</span>
                  <span className="text-[12px] text-sub">
                    {formatDate(v.last_modification_timestamp)}
                  </span>
                  <span
                    className={`rounded-full px-1.5 py-0.5 text-[11px] font-medium ${v.is_published ? "bg-green-50 text-green-700" : "bg-app text-sub"}`}
                  >
                    {v.is_published ? "Published" : "Draft"}
                  </span>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}
