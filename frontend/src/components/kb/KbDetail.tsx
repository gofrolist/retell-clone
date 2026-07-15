"use client";

import AddSourceMenu, {
  partitionPendingSources,
  type PendingSource,
} from "@/components/kb/AddSourceMenu";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { api, docsFromRawKb } from "@/lib/api";
import type { KnowledgeBase, KnowledgeDocument } from "@/lib/types";
import { triggerBlobDownload, truncateId } from "@/lib/utils";
import { CheckCircle2, Download, FileText, Trash2 } from "lucide-react";
import { useState } from "react";

const TYPE_STYLES: Record<string, string> = {
  md: "bg-sky-50 text-sky-700 border-sky-100",
  pdf: "bg-rose-50 text-rose-600 border-rose-100",
  doc: "bg-blue-50 text-blue-700 border-blue-100",
  docx: "bg-blue-50 text-blue-700 border-blue-100",
  csv: "bg-emerald-50 text-emerald-700 border-emerald-100",
  html: "bg-amber-50 text-amber-700 border-amber-100",
  txt: "bg-app text-sub border-line",
  url: "bg-violet-50 text-violet-700 border-violet-100",
};

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
  const [adding, setAdding] = useState(false);

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

  async function addSources(sources: PendingSource[]) {
    const { urls, texts, files } = partitionPendingSources(sources);
    setAdding(true);
    setError(null);
    try {
      const raw = await api.addKnowledgeBaseSources(
        kb.knowledge_base_id,
        {
          ...(urls.length ? { knowledge_base_urls: urls } : {}),
          ...(texts.length ? { knowledge_base_texts: texts } : {}),
        },
        files,
      );
      onSourcesChanged(kb.knowledge_base_id, docsFromRawKb(raw));
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add source");
    } finally {
      setAdding(false);
    }
  }

  async function download(doc: KnowledgeDocument) {
    setError(null);
    try {
      const blob = await api.downloadKnowledgeBaseFile(kb.knowledge_base_id, doc.document_id);
      triggerBlobDownload(blob, doc.name);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to download file");
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
          <AddSourceMenu label={adding ? "Adding…" : "Add source"} onAdd={addSources} onError={setError} />
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
          No sources yet. Add web pages, files, or text to ground your agents.
        </div>
      ) : (
        <div className="mt-5 divide-y divide-line rounded-xl border border-line bg-white shadow-sm">
          {kb.documents.map((doc) => (
            <div key={doc.document_id} className="flex items-center gap-3 px-4 py-3">
              <span
                className={`flex size-8 shrink-0 items-center justify-center rounded-lg border text-[10px] font-bold uppercase ${TYPE_STYLES[doc.type] ?? TYPE_STYLES.txt}`}
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
              {doc.file_url && (
                <button
                  onClick={() => download(doc)}
                  className="rounded-md p-1.5 text-faint hover:bg-app hover:text-ink cursor-pointer"
                  aria-label={`Download ${doc.name}`}
                >
                  <Download className="size-4" />
                </button>
              )}
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
    </div>
  );
}
