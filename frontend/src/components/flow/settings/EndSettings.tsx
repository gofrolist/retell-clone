"use client";

import Toggle from "@/components/ui/Toggle";
import { Field, Textarea } from "@/components/ui/Field";
import { withInstruction } from "../flowModel";
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
              // `static_text` is the default for a node that has no
              // instruction at all, matching what `defaultsFor("end")` seeds
              // and what this field's own copy promises ("Closing line",
              // spoken as written). An existing type is preserved, not
              // overwritten — see `withInstruction`.
              patch: {
                instruction: withInstruction(node.instruction, { text: e.target.value }, "static_text"),
              },
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
