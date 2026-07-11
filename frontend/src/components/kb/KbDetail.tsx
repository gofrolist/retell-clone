"use client";

import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { UnderlineTabs } from "@/components/ui/Tabs";
import { api, type RawKnowledgeBase } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import { truncateId } from "@/lib/utils";
import { CheckCircle2, FileText, Plus, Trash2 } from "lucide-react";
import { useState } from "react";

const TYPE_STYLES: Record<string, string> = {
  md: "bg-sky-50 text-sky-700 border-sky-100",
  pdf: "bg-rose-50 text-rose-600 border-rose-100",
  txt: "bg-app text-sub border-line",
  url: "bg-violet-50 text-violet-700 border-violet-100",
};

/**
 * The backend serializes sources as `knowledge_base_sources` (Retell wire
 * shape) with text under `content`, which the lib/api adapter doesn't read
 * yet — map raw responses here so fresh mutations render correctly.
 */
export function docsFromRawKb(raw: RawKnowledgeBase): KnowledgeDocument[] {
  const rec = raw as Record<string, unknown>;
  const sources = (rec.knowledge_base_sources ?? rec.sources ?? []) as {
    source_id: string;
    type: string;
    title?: string;
    url?: string;
    content?: string;
    text?: string;
    filename?: string;
  }[];
  return sources.map((s) => {
    const text = s.content ?? s.text;
    return {
      document_id: s.source_id,
      name: s.title ?? s.url ?? s.filename ?? s.source_id,
      type: s.type === "url" ? "url" : "txt",
      size_kb: text ? Math.max(1, Math.round(text.length / 1024)) : 0,
    };
  });
}

export default function KbDetail({
  kb,
  onDeleted,
  onSourcesChanged,
}: {
  kb: KnowledgeBase;
  onDeleted: () => void;
  onSourcesChanged: (kbId: string, docs: KnowledgeDocument[]) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [sourceType, setSourceType] = useState("url");
  const [url, setUrl] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  async function removeKb() {
    if (!window.confirm(`Delete knowledge base "${kb.knowledge_base_name}"? This cannot be undone.`))
      return;
    setError(null);
    try {
      await api.deleteKnowledgeBase(kb.knowledge_base_id);
      onDeleted();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete knowledge base");
    }
  }

  async function removeSource(doc: KnowledgeDocument) {
    if (!window.confirm(`Delete source "${doc.name}"?`)) return;
    setError(null);
    try {
      await api.deleteKnowledgeBaseSource(kb.knowledge_base_id, doc.document_id);
      onSourcesChanged(
        kb.knowledge_base_id,
        kb.documents.filter((d) => d.document_id !== doc.document_id),
      );
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete source");
    }
  }

  async function addSource() {
    const body =
      sourceType === "url"
        ? { knowledge_base_urls: [url.trim()] }
        : { knowledge_base_texts: [{ title: textTitle.trim(), text: text.trim() }] };
    if (sourceType === "url" && !url.trim()) {
      setAddError("Enter a URL.");
      return;
    }
    if (sourceType === "text" && (!textTitle.trim() || !text.trim())) {
      setAddError("Enter both a title and text.");
      return;
    }
    setAdding(true);
    setAddError(null);
    try {
      const raw = await api.addKnowledgeBaseSources(kb.knowledge_base_id, body);
      onSourcesChanged(kb.knowledge_base_id, docsFromRawKb(raw));
      setUrl("");
      setTextTitle("");
      setText("");
      setAddOpen(false);
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add source");
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-[17px] font-semibold">{kb.knowledge_base_name}</h1>
          <div className="mt-1 flex items-center gap-3 text-[13px] text-sub">
            <CopyId value={kb.knowledge_base_id} display={truncateId(kb.knowledge_base_id, 8)} />
            {kb.uploaded_by && (
              <span className="inline-flex items-center gap-1">
                Last refreshed: {kb.uploaded_by}
                <CheckCircle2 className="size-3.5 text-ok" />
              </span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="size-3.5" /> Add source
          </Button>
          <Button size="sm" variant="danger" onClick={removeKb} aria-label="Delete knowledge base">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
          {error}
        </p>
      )}

      {kb.documents.length === 0 ? (
        <div className="mt-5 rounded-xl border border-line bg-white px-4 py-10 text-center text-[13px] text-sub shadow-sm">
          No sources yet. Add a URL or pasted text to ground your agents.
        </div>
      ) : (
        <div className="mt-5 divide-y divide-line rounded-xl border border-line bg-white shadow-sm">
          {kb.documents.map((doc) => (
            <div key={doc.document_id} className="flex items-center gap-3 px-4 py-3">
              <span
                className={`flex size-8 items-center justify-center rounded-lg border text-[10px] font-bold uppercase ${TYPE_STYLES[doc.type] ?? TYPE_STYLES.txt}`}
              >
                {doc.type}
              </span>
              <div className="min-w-0 grow">
                <div className="flex items-center gap-1.5 truncate text-[13.5px] font-medium">
                  <FileText className="size-3.5 text-faint shrink-0" />
                  {doc.name}
                </div>
                <div className="text-xs text-sub">{doc.size_kb} KB</div>
              </div>
              <button
                onClick={() => removeSource(doc)}
                className="rounded-md p-1.5 text-faint hover:bg-red-50 hover:text-bad cursor-pointer"
                aria-label={`Delete ${doc.name}`}
              >
                <Trash2 className="size-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        title="Add source"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" disabled={adding} onClick={addSource}>
              {adding ? "Adding…" : "Add"}
            </Button>
          </>
        }
      >
        <UnderlineTabs
          tabs={[
            { key: "url", label: "URL" },
            { key: "text", label: "Text" },
          ]}
          active={sourceType}
          onChange={setSourceType}
          className="mb-4"
        />
        {sourceType === "url" ? (
          <Field label="URL">
            <TextInput
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://example.com/docs"
              autoFocus
            />
          </Field>
        ) : (
          <div className="space-y-3">
            <Field label="Title">
              <TextInput
                value={textTitle}
                onChange={(e) => setTextTitle(e.target.value)}
                placeholder="e.g. Refund policy"
                autoFocus
              />
            </Field>
            <Field label="Text">
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                placeholder="Paste the content here…"
                className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
            </Field>
          </div>
        )}
        {addError && <p className="mt-3 text-[13px] text-bad">{addError}</p>}
      </Modal>
    </div>
  );
}
