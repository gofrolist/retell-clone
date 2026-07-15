"use client";

import AddSourceMenu, { partitionPendingSources, type PendingSource } from "@/components/kb/AddSourceMenu";
import KbDetail from "@/components/kb/KbDetail";
import SecondaryPanel from "@/components/shell/SecondaryPanel";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";
import { cn, kbFromBytes } from "@/lib/utils";
import { FileText, Library, Link2, Plus, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export default function KnowledgeBasePage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [pending, setPending] = useState<PendingSource[]>([]);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const list = await api.listKnowledgeBases();
    setKbs(list);
    return list;
  }, []);

  useEffect(() => {
    refresh()
      .then((list) => {
        setSelected((s) => s ?? list[0]?.knowledge_base_id ?? null);
        setError(null);
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load knowledge bases"),
      )
      .finally(() => setLoading(false));
  }, [refresh]);

  async function create() {
    const kbName = name.trim();
    if (!kbName) {
      setCreateError("Name is required.");
      return;
    }
    const { urls, texts, files } = partitionPendingSources(pending);
    setCreating(true);
    setCreateError(null);
    try {
      const raw = await api.createKnowledgeBase(
        {
          knowledge_base_name: kbName,
          ...(urls.length ? { knowledge_base_urls: urls } : {}),
          ...(texts.length ? { knowledge_base_texts: texts } : {}),
        },
        files,
      );
      await refresh();
      setSelected(raw.knowledge_base_id);
      setName("");
      setPending([]);
      setCreateOpen(false);
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : "Failed to create knowledge base");
    } finally {
      setCreating(false);
    }
  }

  function pendingLabel(p: PendingSource): string {
    if (p.kind === "url") return p.url;
    if (p.kind === "text") return p.title;
    return p.file.name;
  }

  function pendingMeta(p: PendingSource): string {
    if (p.kind === "url") return "Web page";
    if (p.kind === "text") return "Text";
    return `${kbFromBytes(p.file.size)} KB`;
  }

  const kb = kbs.find((k) => k.knowledge_base_id === selected);

  return (
    <SecondaryPanel
      panel={
        <div className="p-3">
          <div className="mb-2 flex items-center justify-between px-2 pt-1">
            <span className="inline-flex items-center gap-2 text-[13.5px] font-semibold">
              <Library className="size-4 text-sub" strokeWidth={1.8} />
              Knowledge Base
            </span>
            <button
              onClick={() => setCreateOpen(true)}
              className="flex size-6 items-center justify-center rounded-md bg-ink text-white hover:bg-black/80 cursor-pointer"
              aria-label="Create knowledge base"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          {loading && <p className="px-3 py-2 text-[13px] text-sub">Loading…</p>}
          {error && <p className="px-3 py-2 text-[13px] text-bad">{error}</p>}
          <div className="space-y-0.5">
            {kbs.map((k) => (
              <button
                key={k.knowledge_base_id}
                onClick={() => setSelected(k.knowledge_base_id)}
                className={cn(
                  "flex w-full items-center rounded-lg px-3 py-2 text-left text-[13.5px] transition-colors cursor-pointer",
                  selected === k.knowledge_base_id
                    ? "bg-white font-medium shadow-sm border border-line"
                    : "text-sub hover:bg-black/4 hover:text-ink border border-transparent",
                )}
              >
                <span className="truncate">{k.knowledge_base_name}</span>
              </button>
            ))}
          </div>
        </div>
      }
    >
      {kb ? (
        <KbDetail
          kb={kb}
          onDeleted={() => {
            refresh()
              .then((list) => setSelected(list[0]?.knowledge_base_id ?? null))
              .catch(() => {});
          }}
          onSourcesChanged={(kbId, docs) => {
            setKbs((prev) =>
              prev.map((k) => (k.knowledge_base_id === kbId ? { ...k, documents: docs } : k)),
            );
            refresh().catch(() => {});
          }}
        />
      ) : (
        <EmptyState
          icon={Library}
          title="No knowledge base selected"
          description="Create a knowledge base to ground your agents with documents."
        />
      )}

      <Modal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        title="Add Knowledge Base"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={creating || !name.trim()} onClick={create}>
              {creating ? "Saving…" : "Save"}
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Knowledge Base Name">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter"
              autoFocus
            />
          </Field>
          <div>
            <div className="mb-1.5 text-[13px] font-medium">Documents</div>
            <AddSourceMenu
              onAdd={(sources) => {
                setPending((prev) => [...prev, ...sources]);
                setCreateError(null);
              }}
              onError={setCreateError}
            />
            {pending.length > 0 && (
              <div className="mt-3 divide-y divide-line rounded-lg border border-line">
                {pending.map((p, i) => (
                  <div key={i} className="flex items-center gap-2.5 px-3 py-2">
                    {p.kind === "url" ? (
                      <Link2 className="size-4 shrink-0 text-sub" strokeWidth={1.8} />
                    ) : (
                      <FileText className="size-4 shrink-0 text-sub" strokeWidth={1.8} />
                    )}
                    <div className="min-w-0 grow">
                      <div className="truncate text-[13px] font-medium">{pendingLabel(p)}</div>
                      <div className="text-xs text-sub">{pendingMeta(p)}</div>
                    </div>
                    <button
                      onClick={() => setPending((prev) => prev.filter((_, j) => j !== i))}
                      className="rounded-md p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
                      aria-label={`Remove ${pendingLabel(p)}`}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
          {createError && <p className="text-[13px] text-bad">{createError}</p>}
        </div>
      </Modal>
    </SecondaryPanel>
  );
}
