"use client";

import Button from "@/components/ui/Button";
import Modal from "@/components/ui/Modal";
import { PillTabs } from "@/components/ui/Tabs";
import { downloadAgentJson, duplicateAgent, duplicateChatAgent } from "@/lib/agentTransfer";
import { api, type RawAgent, type RawLlm } from "@/lib/api";
import {
  Check,
  ChevronLeft,
  Copy,
  Download,
  History,
  MessageSquare,
  MoreHorizontal,
  Share2,
  Sparkles,
  Tag,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

/** Autosave lifecycle of the draft, surfaced next to the version chip. */
export type SaveState = "idle" | "pending" | "saving" | "saved" | "error";

const SAVE_LABEL: Record<SaveState, string | null> = {
  idle: null,
  pending: "Unsaved changes",
  saving: "Saving…",
  saved: "Saved",
  error: null, // the error text itself is shown instead
};

export default function EditorHeader({
  name,
  onName,
  agent,
  llm,
  tab,
  onTab,
  saveState,
  publishing,
  onPublish,
  viewingVersion,
  readOnly,
  onBranch,
  panelOpen,
  onTogglePanel,
  error,
  chat = false,
}: {
  name: string;
  onName: (v: string) => void;
  agent: RawAgent;
  llm: RawLlm | null;
  /** "create" (prompt + settings) or "simulation" (tests + manual testing). */
  tab: string;
  onTab: (key: string) => void;
  saveState: SaveState;
  publishing: boolean;
  onPublish: (meta: { version_title?: string; version_description?: string }) => void;
  /** Version currently open in the editor. */
  viewingVersion: number;
  /** True when viewing a frozen (published, non-latest) version. */
  readOnly: boolean;
  onBranch: () => void;
  panelOpen: boolean;
  onTogglePanel: () => void;
  error?: string | null;
  /** Chat agent: no voice, no versions and no publish flow to show. */
  chat?: boolean;
}) {
  const router = useRouter();
  const [shared, setShared] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [publishOpen, setPublishOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
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

  const exportJson = async () => {
    setMoreOpen(false);
    setMenuError(null);
    try {
      // Async since the export inlines the conversation flow, which has to be
      // fetched — a failure has to surface rather than silently download an
      // agent whose graph is missing.
      await downloadAgentJson(agent, llm);
    } catch (e) {
      setMenuError(e instanceof Error ? e.message : "Failed to export agent");
    }
  };

  const duplicate = async () => {
    if (busy) return;
    setBusy(true);
    setMenuError(null);
    try {
      const created = chat
        ? await duplicateChatAgent(agent, llm)
        : await duplicateAgent(agent, llm);
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
      if (chat) await api.deleteChatAgent(agent.agent_id);
      else await api.deleteAgent(agent.agent_id);
      router.push("/agents");
    } catch (e) {
      // Backend 409s when a phone number is still bound to this agent.
      setMenuError(e instanceof Error ? e.message : "Failed to delete agent");
      setBusy(false);
    }
  };

  const confirmPublish = () => {
    onPublish({
      version_title: title.trim() || undefined,
      version_description: description.trim() || undefined,
    });
    setPublishOpen(false);
    setTitle("");
    setDescription("");
  };

  const saveLabel = SAVE_LABEL[saveState];

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
        readOnly={readOnly}
        className="w-56 truncate rounded-md border border-transparent bg-transparent px-1.5 py-0.5 text-[15px] font-semibold outline-none transition-colors hover:border-line focus:border-accent read-only:hover:border-transparent"
      />
      <span className="inline-flex shrink-0 items-center gap-1 rounded-md border border-line bg-app px-2 py-0.5 text-xs font-medium text-sub">
        {chat ? <MessageSquare className="size-3" /> : <Tag className="size-3" />}
        {chat ? "Chat agent" : agent.is_published ? "Published" : "Draft"}
      </span>

      <div className="mx-auto">
        <PillTabs
          tabs={[
            { key: "create", label: "Create" },
            // Chat agents have no calls to simulate — the tab is the text test.
            { key: "simulation", label: chat ? "Test" : "Simulation" },
          ]}
          active={tab}
          onChange={onTab}
        />
      </div>

      {error ? (
        <span className="max-w-56 truncate text-xs text-bad" title={error}>
          {error}
        </span>
      ) : (
        saveLabel && <span className="shrink-0 text-xs text-faint">{saveLabel}</span>
      )}
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
              {menuError && <p className="px-2 pt-1.5 text-[12px] text-bad">{menuError}</p>}
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
      {/* Versioning and publishing are voice-agent features: the chat-agent
          API has no version history and saves go live immediately. */}
      {!chat && (
        <>
          <Button
            size="sm"
            onClick={onTogglePanel}
            aria-pressed={panelOpen}
            className={panelOpen ? "border-accent text-accent-deep" : undefined}
            title="Version history"
          >
            <History className="size-3.5" />V{viewingVersion}
          </Button>
          {readOnly ? (
            <Button size="sm" variant="primary" onClick={onBranch} disabled={publishing}>
              Edit as new draft
            </Button>
          ) : (
            <Button
              size="sm"
              variant="primary"
              onClick={() => setPublishOpen(true)}
              disabled={publishing}
            >
              {publishing ? "Publishing…" : "Publish"}
            </Button>
          )}
        </>
      )}
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
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title={`Publish V${viewingVersion}`}
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setPublishOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={confirmPublish}>
              Publish
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-sub">
          Live calls switch to this version. It becomes immutable — the next edit opens a new
          draft.
        </p>
        <label className="mt-3 block text-[12px] font-medium text-sub">
          Title <span className="font-normal text-faint">(optional)</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Hotfix"
            className="mt-1 h-9 w-full rounded-lg border border-line px-2.5 text-[13px] font-normal text-ink outline-none focus:border-accent"
          />
        </label>
        <label className="mt-3 block text-[12px] font-medium text-sub">
          What changed? <span className="font-normal text-faint">(optional)</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Shown in the version history."
            className="mt-1 w-full resize-y rounded-lg border border-line px-2.5 py-2 text-[13px] font-normal text-ink outline-none focus:border-accent"
          />
        </label>
      </Modal>

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
          This permanently deletes{" "}
          <span className="font-medium text-ink">{agent.agent_name ?? "this agent"}</span>.
          {chat ? "" : " Phone numbers still routed to it must be re-pointed first."}
        </p>
        {menuError && <p className="mt-2 text-[13px] text-bad">{menuError}</p>}
      </Modal>
    </header>
  );
}
