"use client";

import { CheckboxRow } from "@/components/ui/RadioRow";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";
import { Library } from "lucide-react";
import { useEffect, useState } from "react";

export default function KnowledgeBaseSection({
  attachedIds,
  onChange,
}: {
  attachedIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .listKnowledgeBases()
      .then((k) => !cancelled && setKbs(k))
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Failed to load knowledge bases");
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const toggle = (id: string, checked: boolean) =>
    onChange(checked ? [...attachedIds, id] : attachedIds.filter((x) => x !== id));

  if (loading) return <p className="text-[13px] text-sub">Loading knowledge bases…</p>;
  if (error) return <p className="text-[13px] text-bad">{error}</p>;
  if (kbs.length === 0) {
    return <p className="text-[13px] text-sub">No knowledge bases in this workspace yet.</p>;
  }

  return (
    <div className="space-y-0.5">
      {kbs.map((kb) => (
        <CheckboxRow
          key={kb.knowledge_base_id}
          checked={attachedIds.includes(kb.knowledge_base_id)}
          onChange={(v) => toggle(kb.knowledge_base_id, v)}
          label={
            <span className="flex items-center gap-2">
              <Library className="size-4 text-sub shrink-0" strokeWidth={1.8} />
              <span className="truncate font-medium">{kb.knowledge_base_name}</span>
              <span className="text-xs text-faint">{kb.documents.length} docs</span>
            </span>
          }
        />
      ))}
    </div>
  );
}
