"use client";

import { PairRows, toPairs, fromPairs, type Pair } from "@/components/editor/PairRows";
import Button from "@/components/ui/Button";
import { Field } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { CheckboxRow, RadioRow } from "@/components/ui/RadioRow";
import Toggle from "@/components/ui/Toggle";
import type { PiiConfig } from "@/lib/api";
import type { Voice } from "@/lib/types";
import { ChevronRight } from "lucide-react";
import { useState } from "react";

function SetUpRow({
  label,
  hint,
  status,
  onClick,
}: {
  label: string;
  hint?: string;
  /** Replaces "Set Up" when the feature is already configured. */
  status?: string;
  onClick?: () => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 py-1">
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <p className="text-xs text-sub">{hint}</p>}
      </div>
      {onClick ? (
        <button
          onClick={onClick}
          className="inline-flex items-center gap-0.5 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer whitespace-nowrap"
        >
          {status ?? "Set Up"} <ChevronRight className="size-3.5" />
        </button>
      ) : (
        <button
          disabled
          title="Not available yet"
          className="inline-flex items-center gap-0.5 text-[13px] font-medium text-accent-deep opacity-40 cursor-not-allowed whitespace-nowrap"
        >
          Set Up <ChevronRight className="size-3.5" />
        </button>
      )}
    </div>
  );
}

const PII_CATEGORIES: { value: string; label: string }[] = [
  { value: "person_name", label: "Person name" },
  { value: "address", label: "Address" },
  { value: "email", label: "Email" },
  { value: "phone_number", label: "Phone number" },
  { value: "ssn", label: "SSN" },
  { value: "passport", label: "Passport" },
  { value: "driver_license", label: "Driver license" },
  { value: "credit_card", label: "Credit card" },
];

function PiiRedactionRow({
  value,
  onChange,
}: {
  value: PiiConfig | null;
  onChange: (v: PiiConfig | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [categories, setCategories] = useState<string[]>([]);
  const count = value?.categories.length ?? 0;

  const openEditor = () => {
    setCategories(value?.categories ?? []);
    setOpen(true);
  };
  const save = () => {
    onChange(categories.length ? { mode: "post_call", categories } : null);
    setOpen(false);
  };

  return (
    <>
      <SetUpRow
        label="PII Redaction"
        hint="Redact sensitive data from transcripts."
        status={count > 0 ? `Configured (${count})` : undefined}
        onClick={openEditor}
      />
      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="PII Redaction"
        width="max-w-md"
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
          Selected categories are redacted from transcripts and analysis after each call
          (post-call mode). Selecting none turns redaction off.
        </p>
        <div className="grid grid-cols-2 gap-x-4">
          {PII_CATEGORIES.map((c) => (
            <CheckboxRow
              key={c.value}
              checked={categories.includes(c.value)}
              onChange={(v) =>
                setCategories((cur) =>
                  v ? [...cur, c.value] : cur.filter((x) => x !== c.value),
                )
              }
              label={c.label}
            />
          ))}
        </div>
      </Modal>
    </>
  );
}

function FallbackVoiceRow({
  value,
  onChange,
  voices,
}: {
  value: string[] | null;
  onChange: (v: string[] | null) => void;
  voices: Voice[];
}) {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const automatic = !value || value.length === 0;

  const openEditor = () => {
    setSelected(value ?? []);
    setOpen(true);
  };
  const save = () => {
    onChange(selected.length ? selected : null);
    setOpen(false);
  };

  return (
    <div className="py-1">
      <div className="text-[13px] font-medium">Fallback Voice</div>
      <p className="text-xs text-sub">Voice to use when the primary voice fails.</p>
      <div className="mt-1.5">
        <RadioRow checked={automatic} onSelect={() => onChange(null)} label="Automatic" />
        <RadioRow
          checked={!automatic}
          onSelect={openEditor}
          label={
            automatic ? (
              "Select fallback voices"
            ) : (
              <span>
                Selected{" "}
                <span className="text-sub">
                  · {value!.length} voice{value!.length === 1 ? "" : "s"}
                </span>{" "}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    openEditor();
                  }}
                  className="font-medium text-accent-deep hover:underline cursor-pointer"
                >
                  Edit
                </button>
              </span>
            )
          }
        />
      </div>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Fallback Voices"
        width="max-w-md"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={save} disabled={selected.length === 0}>
              Save
            </Button>
          </>
        }
      >
        <p className="mb-3 text-xs text-sub">
          Tried in order when the primary voice provider fails. Pick voices from a different
          provider than the primary voice for real redundancy.
        </p>
        <div className="max-h-72 overflow-y-auto">
          {voices.map((v) => (
            <CheckboxRow
              key={v.voice_id}
              checked={selected.includes(v.voice_id)}
              onChange={(on) =>
                setSelected((cur) =>
                  on ? [...cur, v.voice_id] : cur.filter((x) => x !== v.voice_id),
                )
              }
              label={
                <span>
                  {v.voice_name} <span className="text-sub">· {v.provider}</span>
                </span>
              }
            />
          ))}
          {voices.length === 0 && (
            <p className="py-4 text-center text-[13px] text-sub">No voices available.</p>
          )}
        </div>
      </Modal>
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
  piiConfig,
  onPiiConfig,
  fallbackVoiceIds,
  onFallbackVoiceIds,
  optInSignedUrl,
  onOptInSignedUrl,
  voices,
  dynamicVariables,
  onDynamicVariables,
}: {
  optOut: boolean;
  onOptOut: (v: boolean) => void;
  piiConfig: PiiConfig | null;
  onPiiConfig: (v: PiiConfig | null) => void;
  fallbackVoiceIds: string[] | null;
  onFallbackVoiceIds: (v: string[] | null) => void;
  optInSignedUrl: boolean;
  onOptInSignedUrl: (v: boolean) => void;
  voices: Voice[];
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

      <PiiRedactionRow value={piiConfig} onChange={onPiiConfig} />
      <SetUpRow label="Safety Guardrails" hint="Constrain what the agent can say." />

      <div className="flex items-center justify-between gap-4">
        <div>
          <div className="text-[13px] font-medium">Opt In Secure URLs</div>
          <p className="text-xs text-sub">Sign asset URLs with short expiry.</p>
        </div>
        <Toggle checked={optInSignedUrl} onChange={onOptInSignedUrl} />
      </div>

      <FallbackVoiceRow
        value={fallbackVoiceIds}
        onChange={onFallbackVoiceIds}
        voices={voices}
      />
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
