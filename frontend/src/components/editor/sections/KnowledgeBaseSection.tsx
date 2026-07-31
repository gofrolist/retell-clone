"use client";

import { Field, TextInput } from "@/components/ui/Field";
import { CheckboxRow } from "@/components/ui/RadioRow";
import { api } from "@/lib/api";
import type { KnowledgeBase } from "@/lib/types";
import { Library } from "lucide-react";
import { useEffect, useState } from "react";

export default function KnowledgeBaseSection({
  attachedIds,
  onChange,
  kbConfig,
  onKbConfig,
}: {
  attachedIds: string[];
  onChange: (ids: string[]) => void;
  /**
   * Flow-only tuning for the flow's `kb_lookup` tool (`{top_k, filter_score}`
   * on the wire). Optional and left `Record<string, unknown>` — never
   * reconstructed as a closed `{top_k, filter_score}` object — so an
   * imported flow's extra keys here round-trip untouched. Omit both this and
   * `onKbConfig` for an LLM agent, which has no equivalent; the controls
   * disappear with them, same idiom as `SelectorRow`'s optional voice/
   * timezone handlers.
   */
  kbConfig?: Record<string, unknown> | null;
  onKbConfig?: (v: Record<string, unknown>) => void;
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

  const topK = typeof kbConfig?.top_k === "number" ? kbConfig.top_k : undefined;
  const filterScore = typeof kbConfig?.filter_score === "number" ? kbConfig.filter_score : undefined;

  // Spread the existing sub-object when patching one key — never replace it
  // — so an imported flow's unmodeled kb_config keys survive an edit here.
  const patchKbConfig = (patch: Record<string, unknown>) =>
    onKbConfig?.({ ...(kbConfig ?? {}), ...patch });

  const tuning = onKbConfig && (
    <div className="mt-3 space-y-3 border-t border-line pt-3">
      <Field
        label="Results per lookup"
        hint="How many knowledge-base matches the flow's kb_lookup tool returns (top_k)."
      >
        <TextInput
          type="number"
          min={1}
          max={20}
          value={topK ?? ""}
          placeholder="3"
          onChange={(e) =>
            patchKbConfig({ top_k: e.target.value === "" ? undefined : Number(e.target.value) })
          }
        />
      </Field>
      <Field
        label="Minimum match score"
        hint="0–1. Saved with the flow for forward compatibility; the search backend does not filter on it yet."
      >
        <TextInput
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={filterScore ?? ""}
          placeholder="0.6"
          onChange={(e) =>
            patchKbConfig({
              filter_score: e.target.value === "" ? undefined : Number(e.target.value),
            })
          }
        />
      </Field>
    </div>
  );

  if (loading) {
    return (
      <div>
        <p className="text-[13px] text-sub">Loading knowledge bases…</p>
        {tuning}
      </div>
    );
  }
  if (error) {
    return (
      <div>
        <p className="text-[13px] text-bad">{error}</p>
        {tuning}
      </div>
    );
  }
  if (kbs.length === 0) {
    return (
      <div>
        <p className="text-[13px] text-sub">No knowledge bases in this workspace yet.</p>
        {tuning}
      </div>
    );
  }

  return (
    <div>
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
      {tuning}
    </div>
  );
}
