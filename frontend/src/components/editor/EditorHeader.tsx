"use client";

import VersionHistory from "@/components/editor/VersionHistory";
import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { PillTabs } from "@/components/ui/Tabs";
import { api, type RawAgent, type RawLlm } from "@/lib/api";
import {
  Check,
  ChevronLeft,
  Copy,
  Download,
  History,
  MoreHorizontal,
  Share2,
  Sparkles,
  Tag,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

export default function EditorHeader({
  name,
  onName,
  agent,
  llm,
  dirty,
  saving,
  onSave,
  publishing,
  onPublish,
  error,
}: {
  name: string;
  onName: (v: string) => void;
  agent: RawAgent;
  llm: RawLlm | null;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  publishing: boolean;
  onPublish: () => void;
  error?: string | null;
}) {
  const router = useRouter();
  const [tab, setTab] = useState("create");
  const [shared, setShared] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [menuError, setMenuError] = useState<string | null>(null);

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setShared(true);
      setTimeout(() => setShared(false), 1500);
    } catch {
      // Clipboard blocked (permissions); nothing useful to do.
    }
  };

  const exportJson = () => {
    const blob = new Blob([JSON.stringify({ agent, llm }, null, 2)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${(agent.agent_name ?? agent.agent_id).replace(/[^\w.-]+/g, "_")}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
    setMoreOpen(false);
  };

  const duplicate = async () => {
    if (busy) return;
    setBusy(true);
    setMenuError(null);
    try {
      // Spread the full objects rather than hand-picking fields: the backend
      // create endpoints keep only mutable fields, so every copyable setting
      // (including ones added later) transfers without this list drifting.
      let llmId: string | undefined;
      if (llm) {
        const copy = await api.createLlm({ ...llm });
        llmId = copy.llm_id;
      }
      const created = await api.createAgent({
        ...agent,
        agent_name: `Copy of ${agent.agent_name ?? "Untitled agent"}`,
        response_engine: llmId ? { type: "retell-llm", llm_id: llmId } : agent.response_engine,
      });
      router.push(`/agents/${created.agent_id}`);
    } catch (e) {
      setMenuError(e instanceof Error ? e.message : "Failed to duplicate agent");
    } finally {
      setBusy(false);
    }
  };

  const deleteAgent = async () => {
    if (busy) return;
    setBusy(true);
    setMenuError(null);
    try {
      await api.deleteAgent(agent.agent_id);
      router.push("/agents");
    } catch (e) {
      // Backend 409s when a phone number is still bound to this agent.
      setMenuError(e instanceof Error ? e.message : "Failed to delete agent");
      setBusy(false);
    }
  };

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-card px-4">
      <Link
        href="/agents"
        className="rounded-md p-1.5 text-sub hover:bg-app"
        aria-label="Back to agents"
      >
        <ChevronLeft className="size-4.5" />
      </Link>
      <input
        value={name}
        onChange={(e) => onName(e.target.value)}
        placeholder="Untitled agent"
        aria-label="Agent name"
        className="w-64 truncate rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[15px] font-semibold outline-none transition-colors hover:border-line focus:border-accent"
      />
      <span className="inline-flex items-center gap-1 rounded-md border border-line bg-app px-2 py-0.5 text-xs font-medium text-sub">
        <Tag className="size-3" />
        {agent.is_published ? "Published" : "Draft"}
      </span>

      <div className="mx-auto">
        {/* Simulation has no backend yet; only the Create view exists. */}
        <PillTabs
          tabs={[{ key: "create", label: "Create" }]}
          active={tab}
          onChange={setTab}
        />
      </div>

      {error && <span className="max-w-64 truncate text-xs text-bad" title={error}>{error}</span>}
      <div className="relative">
        <button
          onClick={() => setMoreOpen((v) => !v)}
          className="rounded-md p-1.5 text-sub hover:bg-app cursor-pointer"
          aria-label="More"
        >
          <MoreHorizontal className="size-4" />
        </button>
        {moreOpen && (
          <>
            <div className="fixed inset-0 z-20" onClick={() => setMoreOpen(false)} />
            <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-xl border border-line bg-white p-2 shadow-lg">
              <button
                onClick={() => void duplicate()}
                disabled={busy}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer disabled:opacity-50"
              >
                <Copy className="size-3.5 text-sub" />
                {busy ? "Working…" : "Duplicate agent"}
              </button>
              <button
                onClick={exportJson}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
              >
                <Download className="size-3.5 text-sub" /> Export JSON
              </button>
              <button
                onClick={() => {
                  setMoreOpen(false);
                  setMenuError(null);
                  setDeleteOpen(true);
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] text-bad hover:bg-red-50 cursor-pointer"
              >
                <Trash2 className="size-3.5" /> Delete agent
              </button>
              {menuError && (
                <p className="px-2 pt-1.5 text-[12px] text-bad">{menuError}</p>
              )}
            </div>
          </>
        )}
      </div>
      <button
        onClick={() => void share()}
        className="rounded-md p-1.5 text-sub hover:bg-app cursor-pointer"
        aria-label="Share"
        title="Copy link to this agent"
      >
        {shared ? <Check className="size-4 text-ok" /> : <Share2 className="size-4" />}
      </button>
      <VersionHistory
        agentId={agent.agent_id}
        trigger={(open) => (
          <Button size="sm" onClick={open}>
            <History className="size-3.5" />
            V{agent.version}
          </Button>
        )}
      />
      <Button size="sm" variant="primary" onClick={onSave} disabled={!dirty || saving}>
        {saving ? "Saving…" : "Save"}
      </Button>
      <Button
        size="sm"
        onClick={onPublish}
        disabled={publishing}
        title={dirty ? "You have unsaved changes; Publish uses the last saved version" : undefined}
      >
        {publishing ? "Publishing…" : "Publish"}
      </Button>
      <Button
        size="sm"
        variant="secondary"
        className="text-accent-deep opacity-40 cursor-not-allowed"
        disabled
        title="Not available yet"
      >
        <Sparkles className="size-3.5" />
        Conductor
      </Button>

      <Modal
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Delete Agent"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={() => void deleteAgent()} disabled={busy}>
              {busy ? "Deleting…" : "Delete permanently"}
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-sub">
          This permanently deletes <span className="font-medium text-ink">{agent.agent_name ?? "this agent"}</span>.
          Phone numbers still routed to it must be re-pointed first.
        </p>
        {menuError && <p className="mt-2 text-[13px] text-bad">{menuError}</p>}
      </Modal>
    </header>
  );
}
