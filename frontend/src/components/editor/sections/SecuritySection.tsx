"use client";

import { Field } from "@/components/ui/Field";
import { RadioRow } from "@/components/ui/RadioRow";
import Toggle from "@/components/ui/Toggle";
import { ChevronRight } from "lucide-react";

function SetUpRow({ label, hint }: { label: string; hint?: string }) {
  return (
    <div className="flex items-center justify-between gap-4 py-1">
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <p className="text-xs text-sub">{hint}</p>}
      </div>
      <button
        disabled
        title="Not available yet"
        className="inline-flex items-center gap-0.5 text-[13px] font-medium text-accent-deep opacity-40 cursor-not-allowed whitespace-nowrap"
      >
        Set Up <ChevronRight className="size-3.5" />
      </button>
    </div>
  );
}

export default function SecuritySection({
  optOut,
  onOptOut,
}: {
  optOut: boolean;
  onOptOut: (v: boolean) => void;
}) {
  return (
    <div className="space-y-5">
      <Field label="Data Storage">
        <RadioRow
          checked={!optOut}
          onSelect={() => onOptOut(false)}
          label={
            <span>
              Everything{" "}
              <span className="text-sub">· Retention: Keep forever</span>
            </span>
          }
        />
        <RadioRow
          checked={optOut}
          onSelect={() => onOptOut(true)}
          label="Opt out of sensitive data storage"
        />
      </Field>

      <SetUpRow label="PII Redaction" hint="Redact sensitive data from transcripts." />
      <SetUpRow label="Safety Guardrails" hint="Constrain what the agent can say." />

      <div className="flex items-center justify-between gap-4 opacity-50" title="Not available yet">
        <div>
          <div className="text-[13px] font-medium">Opt In Secure URLs</div>
          <p className="text-xs text-sub">Sign asset URLs with short expiry.</p>
        </div>
        <Toggle checked={false} disabled />
      </div>

      <SetUpRow label="Fallback Voice" hint="Voice to use when the primary voice fails." />
      <SetUpRow label="Default Dynamic Variables" />
    </div>
  );
}
