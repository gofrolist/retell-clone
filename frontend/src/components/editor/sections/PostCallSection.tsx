"use client";

import { Field } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import { GripVertical } from "lucide-react";
import { POST_CALL_ANALYSIS_MODELS } from "@/lib/models";

// These reflect the analysis fields the backend actually produces on
// call_analysis (summary, call_successful, user_sentiment).
const ROWS = [
  { name: "Call Summary", type: "text" },
  { name: "Call Successful", type: "boolean" },
  { name: "User Sentiment", type: "enum" },
];

export default function PostCallSection({
  model,
  onModel,
}: {
  model: string;
  onModel: (v: string) => void;
}) {
  const knownModels = POST_CALL_ANALYSIS_MODELS.map((m) => ({ value: m.id, label: m.label }));
  const options = POST_CALL_ANALYSIS_MODELS.some((m) => m.id === model)
    ? knownModels
    : [{ value: model, label: model }, ...knownModels];
  return (
    <div className="space-y-4">
      <div className="divide-y divide-line rounded-lg border border-line bg-white">
        {ROWS.map((r) => (
          <div key={r.name} className="flex items-center gap-2 px-3 py-2.5">
            <GripVertical className="size-3.5 text-faint" />
            <span className="grow text-[13px] font-medium">{r.name}</span>
            <span className="rounded bg-app px-1.5 py-0.5 font-mono text-[11px] text-sub">
              {r.type}
            </span>
          </div>
        ))}
      </div>
      <Field label="Extraction Model">
        <Select value={model} onChange={onModel} className="w-full" options={options} />
      </Field>
    </div>
  );
}
