"use client";

import { Field, TextInput, Textarea } from "@/components/ui/Field";
import Toggle from "@/components/ui/Toggle";
import { cn } from "@/lib/utils";
import { transferNumberStatus } from "../flowModel";
import type { NodeSettingsProps } from "./NodeSettings";

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
 * honest substitute: it catches an obviously malformed fixed number at author
 * time. It cannot judge a `{{variable}}` destination — the worker resolves
 * templates before it validates — so a templated value is reported as
 * pending resolution, never as an error.
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
  // `_transfer_number` resolves `{{…}}` BEFORE it matches E.164, so a
  // templated number is a working configuration whose final value simply is
  // not knowable at author time — `flowModel.transferNumberStatus` reports it
  // as `template`, and only a placeholder-free non-E.164 value is an error.
  const numberStatus = transferNumberStatus(number);

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
          hint="Must be E.164 (e.g. +14155551234) once dynamic variables are resolved. That check is unconditional at call time with no override — a non-matching number silently takes the failure edge below instead of dialing. {{variables}} are allowed and resolved first."
        >
          <TextInput
            value={number}
            onChange={(e) => patchDestination({ number: e.target.value })}
            placeholder="+14155551234"
            className={cn(numberStatus === "invalid" && "border-bad focus:border-bad")}
          />
          {numberStatus === "invalid" && (
            <p className="mt-1.5 text-xs text-bad">
              Not E.164 — the call will take the failure edge below instead of dialing.
            </p>
          )}
          {numberStatus === "template" && (
            <p className="mt-1.5 text-xs text-sub">
              Resolved from dynamic variables at call time, then checked for E.164 — valid as
              long as it resolves to a plain E.164 number.
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
