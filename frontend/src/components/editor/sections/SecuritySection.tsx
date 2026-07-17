"use client";

import { PairRows, toPairs, fromPairs, type Pair } from "@/components/editor/PairRows";
import Button from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { RadioRow } from "@/components/ui/RadioRow";
import Toggle from "@/components/ui/Toggle";
import { ChevronRight } from "lucide-react";
import { useState } from "react";

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

/**
 * Editable "Default Dynamic Variables" row: fallback values for `{{var}}`
 * placeholders when a call doesn't supply them (defaults < per-call vars).
 * Falls back to the disabled stub when the agent has no editable LLM engine
 * (conversation-flow agents edit their engine elsewhere).
 */
function DynamicVariablesRow({
  value,
  onChange,
}: {
  value: Record<string, unknown> | null;
  onChange: (v: Record<string, string> | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pairs, setPairs] = useState<Pair[]>([]);
  const count = value ? Object.keys(value).length : 0;

  const openEditor = () => {
    setPairs(toPairs(value));
    setOpen(true);
  };
  const save = () => {
    onChange(fromPairs(pairs) ?? null);
    setOpen(false);
  };

  return (
    <div className="flex items-center justify-between gap-4 py-1">
      <div>
        <div className="text-[13px] font-medium">Default Dynamic Variables</div>
        <p className="text-xs text-sub">
          Fallback values used when a variable isn&apos;t provided for a call.
        </p>
      </div>
      <button
        onClick={openEditor}
        className="inline-flex items-center gap-0.5 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer whitespace-nowrap"
      >
        {count > 0 ? `Edit (${count})` : "Set Up"} <ChevronRight className="size-3.5" />
      </button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Default Dynamic Variables"
        width="max-w-lg"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={save}>
              Save
            </Button>
          </>
        }
      >
        <p className="mb-3 text-xs text-sub">
          Set fallback values for dynamic variables across all endpoints if they
          are not provided at call time.
        </p>
        {pairs.length > 0 && (
          <div className="mb-1.5 flex gap-1.5 px-0.5 text-xs font-medium text-sub">
            <span className="flex-1">Variable Name</span>
            <span className="flex-1">Default Value</span>
            <span className="w-6 shrink-0" />
          </div>
        )}
        <PairRows
          addLabel="Add variable"
          pairs={pairs}
          onChange={setPairs}
          keyPlaceholder="Variable name"
          valuePlaceholder="Default value"
        />
      </Modal>
    </div>
  );
}

export default function SecuritySection({
  optOut,
  onOptOut,
  dynamicVariables,
  onDynamicVariables,
}: {
  optOut: boolean;
  onOptOut: (v: boolean) => void;
  dynamicVariables?: Record<string, unknown> | null;
  onDynamicVariables?: (v: Record<string, string> | null) => void;
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
      {onDynamicVariables ? (
        <DynamicVariablesRow
          value={dynamicVariables ?? null}
          onChange={onDynamicVariables}
        />
      ) : (
        <SetUpRow label="Default Dynamic Variables" />
      )}
    </div>
  );
}
