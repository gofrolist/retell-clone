"use client";

import { Field, Textarea } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import { cn } from "@/lib/utils";
import { withInstruction } from "../flowModel";
import type { NodeSettingsProps } from "./NodeSettings";

/**
 * Labels are honest about what each instruction type means at runtime
 * (`worker/src/arhiteq_worker/flow.py:node_instructions`/`static_text`):
 * `prompt` is handed to the model to phrase, `static_text` is spoken
 * verbatim with no model turn involved.
 */
const INSTRUCTION_TYPES = [
  { value: "prompt", label: "Prompt", hint: "The model phrases it." },
  { value: "static_text", label: "Exact text", hint: "Spoken verbatim." },
] as const;

const START_SPEAKER_OPTIONS = [
  { value: "", label: "Inherit from flow" },
  { value: "agent", label: "Agent speaks first" },
  { value: "user", label: "Wait for the caller" },
];

/**
 * Shared by `conversation` and `subagent` nodes — both are the worker's
 * "speaking" node types (`_enter_conversation` handles both identically) and
 * carry the same fields.
 */
export default function ConversationSettings({ node, flow, dispatch }: NodeSettingsProps) {
  const instruction = (node.instruction ?? {}) as { type?: string; text?: string };
  const instructionType = instruction.type === "static_text" ? "static_text" : "prompt";
  const rawStartSpeaker = node.start_speaker;
  const startSpeaker = rawStartSpeaker === "agent" || rawStartSpeaker === "user" ? rawStartSpeaker : "";
  const globalSetting = (node.global_node_setting ?? {}) as { condition?: string };
  // `flow.py:start_speaker_for` has exactly one caller — `main.py` applies it
  // to `self._graph.start` when it builds the runtime's config. On any other
  // node the field is stored faithfully and then never read, so offering the
  // control there would be the same lie the branch refused to tell for
  // `ignore_e164_validation` (see `TransferSettings`): a control that does
  // nothing. It appears only on the start node.
  const isStartNode = flow.start_node_id === node.id;

  const patch = (patch: Record<string, unknown>) =>
    dispatch({ type: "patchNode", nodeId: node.id, patch });

  return (
    <div className="space-y-4">
      <Field label="What it says">
        <div className="space-y-2">
          <div className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-app p-0.5">
            {INSTRUCTION_TYPES.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() =>
                  patch({
                    instruction: withInstruction(node.instruction, { type: opt.value }, "prompt"),
                  })
                }
                className={cn(
                  "rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors cursor-pointer",
                  instructionType === opt.value
                    ? "border border-line bg-white text-ink shadow-sm"
                    : "text-sub hover:text-ink",
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="text-xs text-sub">
            {INSTRUCTION_TYPES.find((opt) => opt.value === instructionType)?.hint}
          </p>
          <Textarea
            rows={4}
            value={instruction.text ?? ""}
            // `prompt` when the node carries no instruction at all: it is
            // what `instructionType` above already displays for that case,
            // and what `defaultsFor("conversation")` seeds. Storing text with
            // no type would leave the line silently unspoken.
            onChange={(e) =>
              patch({
                instruction: withInstruction(node.instruction, { text: e.target.value }, "prompt"),
              })
            }
          />
        </div>
      </Field>

      {isStartNode && (
        <Field
          label="Who speaks first"
          hint="Overrides the flow's own start speaker for the opening of the call. Only the start node's value is read at runtime, so this appears here alone."
        >
          <Select
            value={startSpeaker}
            onChange={(v) => patch({ start_speaker: v })}
            options={START_SPEAKER_OPTIONS}
          />
        </Field>
      )}

      <Field
        label="Global node"
        hint="When set, this node is reachable from anywhere in the flow — the caller can jump here on any turn once the condition matches, not only by an authored edge into it."
      >
        <Textarea
          rows={2}
          placeholder="Describe when the caller should be able to jump here from anywhere…"
          value={globalSetting.condition ?? ""}
          onChange={(e) =>
            patch({ global_node_setting: { ...globalSetting, condition: e.target.value } })
          }
        />
      </Field>
    </div>
  );
}
