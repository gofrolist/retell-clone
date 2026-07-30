"use client";

import { Field, TextInput, Textarea } from "@/components/ui/Field";
import Toggle from "@/components/ui/Toggle";
import { cn } from "@/lib/utils";
import type { NodeSettingsProps } from "./NodeSettings";

// worker/src/arhiteq_worker/tools.py:E164_RE — kept in sync by hand since the
// worker's own module cannot be imported client-side.
const E164_RE = /^\+[1-9]\d{1,14}$/;

const DESTINATION_TYPES = [
  { value: "predefined", label: "Fixed number" },
  { value: "inferred", label: "From the conversation" },
] as const;

/**
 * `ignore_e164_validation` is deliberately not surfaced here: the worker's
 * `_transfer_number` (`worker/src/arhiteq_worker/flow_runtime.py`) never
 * honours it — E.164 is enforced unconditionally on every dial-out path
 * (see `docs/SECURITY.md` § Transfer destinations) — so a toggle would claim
 * a control that does nothing. Client-side E.164 validation below is the
 * honest substitute: it cannot stop a bad `{{variable}}` substitution at call
 * time, but it catches an obviously malformed fixed number at author time.
 */
export default function TransferSettings({ node, dispatch }: NodeSettingsProps) {
  const destination = (node.transfer_destination ?? {}) as {
    type?: string;
    number?: string;
    prompt?: string;
  };
  const kind = destination.type === "inferred" ? "inferred" : "predefined";
  const number = typeof destination.number === "string" ? destination.number : "";
  const prompt = typeof destination.prompt === "string" ? destination.prompt : "";
  const speaks = Boolean(node.speak_during_execution);
  const numberLooksValid = number.trim() === "" || E164_RE.test(number.trim());

  const patchNode = (patch: Record<string, unknown>) =>
    dispatch({ type: "patchNode", nodeId: node.id, patch });

  const patchDestination = (patch: Record<string, unknown>) =>
    patchNode({ transfer_destination: { ...destination, ...patch } });

  return (
    <div className="space-y-3">
      <Field label="Destination">
        <div className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-app p-0.5">
          {DESTINATION_TYPES.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => patchDestination({ type: opt.value })}
              className={cn(
                "rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors cursor-pointer",
                kind === opt.value
                  ? "border border-line bg-white text-ink shadow-sm"
                  : "text-sub hover:text-ink",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </Field>

      {kind === "predefined" ? (
        <Field
          label="Number"
          hint="Must be E.164 (e.g. +14155551234). This is enforced unconditionally at call time with no override — a non-matching number silently takes the failure edge below instead of dialing."
        >
          <TextInput
            value={number}
            onChange={(e) => patchDestination({ number: e.target.value })}
            placeholder="+14155551234"
            className={cn(!numberLooksValid && "border-bad focus:border-bad")}
          />
          {!numberLooksValid && (
            <p className="mt-1.5 text-xs text-bad">
              Not E.164 — the call will take the failure edge below instead of dialing.
            </p>
          )}
        </Field>
      ) : (
        <Field
          label="Prompt"
          hint="Describes where to send the call, resolved from dynamic variables at call time (e.g. {{transfer_number}}). It must resolve to a plain E.164 number — there is no model turn available to phrase it further, so anything else falls through to the failure edge."
        >
          <Textarea rows={2} value={prompt} onChange={(e) => patchDestination({ prompt: e.target.value })} />
        </Field>
      )}

      <Field label="Speak while dialing" hint="Says a line before attempting the transfer.">
        <Toggle checked={speaks} onChange={(v) => patchNode({ speak_during_execution: v })} />
      </Field>
    </div>
  );
}
