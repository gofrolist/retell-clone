"use client";

import CreateWorkspaceModal from "@/components/shell/CreateWorkspaceModal";
import SearchInput from "@/components/ui/SearchInput";
import { api, type WorkspaceSummary } from "@/lib/api";
import {
  enterWorkspace,
  getServerSessionSnapshot,
  getSessionSnapshot,
  subscribeSession,
} from "@/lib/auth";
import { cn } from "@/lib/utils";
import { Check, ChevronsUpDown, Plus } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

function WorkspaceAvatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      className={cn(
        "flex items-center justify-center rounded-md bg-accent-deep font-semibold text-white shrink-0",
        className,
      )}
    >
      {(name || "?").charAt(0).toUpperCase()}
    </span>
  );
}

export default function WorkspaceSwitcher() {
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [name, setName] = useState("Arhiteq Workspace");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [switching, setSwitching] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  // Switching requires a session JWT to re-issue; API-key dev mode has none.
  const session = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getServerSessionSnapshot,
  );

  useEffect(() => {
    // The active workspace's name comes from /workspace so the button is
    // correct even if the caller has no membership row to list.
    api
      .getWorkspace()
      .then((ws) => ws.name && setName(ws.name))
      .catch(() => {}); // keep the fallback; the banner covers backend-down
    api.listWorkspaces().then(setWorkspaces).catch(() => {});
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const switchTo = async (ws: WorkspaceSummary) => {
    if (ws.is_current) {
      setOpen(false);
      return;
    }
    setSwitching(ws.workspace_id);
    setError(null);
    try {
      const next = await api.switchWorkspace(ws.workspace_id);
      // Navigates away; the saving state never needs to unwind.
      enterWorkspace({
        token: next.token!,
        expires_at: next.expires_at!,
        workspace_id: next.workspace_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to switch workspace");
      setSwitching(null);
    }
  };

  const needle = query.trim().toLowerCase();
  const shown = needle
    ? workspaces.filter((w) => w.name.toLowerCase().includes(needle))
    : workspaces;

  return (
    <>
      <div ref={rootRef} className="relative">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 rounded-lg border border-line bg-white px-2.5 py-2 shadow-sm hover:bg-app cursor-pointer"
        >
          <WorkspaceAvatar name={name} className="size-5.5 text-[11px]" />
          <span className="grow truncate text-left">
            <span className="block text-[10px] leading-tight text-faint">Workspace</span>
            <span className="block truncate text-[13px] font-medium leading-tight">{name}</span>
          </span>
          <ChevronsUpDown className="size-3.5 text-faint shrink-0" />
        </button>

        {open && (
          <div className="absolute left-0 top-full z-30 mt-1.5 w-full min-w-64 rounded-lg border border-line bg-white p-1 shadow-lg">
            {workspaces.length > 4 && (
              <div className="p-1">
                <SearchInput value={query} onChange={setQuery} />
              </div>
            )}
            <div className="max-h-64 overflow-y-auto">
              {shown.map((ws) => (
                <button
                  key={ws.workspace_id}
                  onClick={() => switchTo(ws)}
                  disabled={switching !== null}
                  className="flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left hover:bg-app disabled:opacity-60 cursor-pointer"
                >
                  <WorkspaceAvatar name={ws.name} className="size-6 text-[11px]" />
                  <span className="grow truncate text-[13px]">{ws.name}</span>
                  {switching === ws.workspace_id ? (
                    <span className="text-[11px] text-sub shrink-0">Switching…</span>
                  ) : (
                    ws.is_current && <Check className="size-3.5 shrink-0 text-ink" />
                  )}
                </button>
              ))}
              {shown.length === 0 && (
                <div className="px-2.5 py-3 text-[12.5px] text-sub">
                  {workspaces.length === 0 ? "No workspaces yet." : "No matches."}
                </div>
              )}
            </div>

            <div className="my-1 border-t border-line" />
            <button
              onClick={() => {
                setOpen(false);
                setCreateOpen(true);
              }}
              disabled={!session}
              title={
                session ? undefined : "Sign in with Google to create another workspace"
              }
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[13px] hover:bg-app disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
            >
              <Plus className="size-3.5 text-sub" /> Add another workspace
            </button>
            {error && <p className="px-2.5 py-1.5 text-[12px] text-bad">{error}</p>}
          </div>
        )}
      </div>

      <CreateWorkspaceModal open={createOpen} onClose={() => setCreateOpen(false)} />
    </>
  );
}
