"use client";

import KbDetail, { docsFromRawKb } from "@/components/kb/KbDetail";
import SecondaryPanel from "@/components/shell/SecondaryPanel";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Library, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export default function KnowledgeBasePage() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // The lib/api list adapter doesn't surface `knowledge_base_sources` yet, so
  // keep the freshest documents we've seen from raw mutation responses.
  const [docsOverride, setDocsOverride] = useState<Record<string, KnowledgeDocument[]>>({});

  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [urlsText, setUrlsText] = useState("");
  const [pastedText, setPastedText] = useState("");
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
    const urls = urlsText
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);
    setCreating(true);
    setCreateError(null);
    try {
      const raw = await api.createKnowledgeBase({
        knowledge_base_name: kbName,
        ...(urls.length ? { knowledge_base_urls: urls } : {}),
        ...(pastedText.trim()
          ? { knowledge_base_texts: [{ title: `${kbName} notes`, text: pastedText.trim() }] }
          : {}),
      });
      setDocsOverride((m) => ({ ...m, [raw.knowledge_base_id]: docsFromRawKb(raw) }));
      await refresh();
      setSelected(raw.knowledge_base_id);
      setName("");
      setUrlsText("");
      setPastedText("");
      setCreateOpen(false);
    } catch (e: unknown) {
      setCreateError(e instanceof Error ? e.message : "Failed to create knowledge base");
    } finally {
      setCreating(false);
    }
  }

  const kbRaw = kbs.find((k) => k.knowledge_base_id === selected);
  const kb = kbRaw
    ? { ...kbRaw, documents: docsOverride[kbRaw.knowledge_base_id] ?? kbRaw.documents }
    : undefined;

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
            setDocsOverride((m) => {
              const { [kb.knowledge_base_id]: _removed, ...rest } = m;
              return rest;
            });
            refresh()
              .then((list) => setSelected(list[0]?.knowledge_base_id ?? null))
              .catch(() => {});
          }}
          onSourcesChanged={(kbId, docs) => {
            setDocsOverride((m) => ({ ...m, [kbId]: docs }));
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
        title="Create Knowledge Base"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={creating || !name.trim()} onClick={create}>
              {creating ? "Creating…" : "Create"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Support docs"
              autoFocus
            />
          </Field>
          <Field label="URLs (optional)" hint="One URL per line.">
            <textarea
              value={urlsText}
              onChange={(e) => setUrlsText(e.target.value)}
              rows={3}
              placeholder={"https://example.com/docs\nhttps://example.com/faq"}
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
            />
          </Field>
          <Field label="Pasted text (optional)">
            <textarea
              value={pastedText}
              onChange={(e) => setPastedText(e.target.value)}
              rows={4}
              placeholder="Paste reference content here…"
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
            />
          </Field>
          {createError && <p className="text-[13px] text-bad">{createError}</p>}
        </div>
      </Modal>
    </SecondaryPanel>
  );
}
