"use client";

import Toggle from "@/components/ui/Toggle";
import { Field, Textarea } from "@/components/ui/Field";
import type { NodeSettingsProps } from "./NodeSettings";

export default function EndSettings({ node, dispatch }: NodeSettingsProps) {
  const instruction = (node.instruction ?? {}) as { type?: string; text?: string };
  const speaks = Boolean(node.speak_during_execution);

  return (
    <>
      <Field
        label="Closing line"
        hint="Only spoken when “Say a closing line” is on — otherwise the call just ends."
      >
        <Textarea
          rows={3}
          value={instruction.text ?? ""}
          onChange={(e) =>
            dispatch({
              type: "patchNode",
              nodeId: node.id,
              // Preserve the instruction's own type: a `prompt` line is
              // phrased by the model, a `static_text` one is spoken verbatim,
              // and silently flipping it changes what the caller hears.
              patch: { instruction: { ...instruction, text: e.target.value } },
            })
          }
        />
      </Field>
      <Field label="Say a closing line" className="mt-3">
        <Toggle
          checked={speaks}
          onChange={(v) =>
            dispatch({ type: "patchNode", nodeId: node.id, patch: { speak_during_execution: v } })
          }
        />
      </Field>
    </>
  );
}
