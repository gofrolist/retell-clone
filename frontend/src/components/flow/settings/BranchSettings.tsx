"use client";

import { Field, Textarea } from "@/components/ui/Field";
import type { NodeSettingsProps } from "./NodeSettings";

/**
 * A branch node is pure routing (`_enter_branch` in
 * `worker/src/arhiteq_worker/flow_runtime.py`): it installs nothing and
 * speaks nothing. Equation edges (on the shared `EdgeList` above this, in
 * `NodeSettings`) are checked first and cost no model call; anything left to
 * a prompt edge costs one extra classification call, which is why the note
 * below calls that out explicitly rather than leaving it implicit.
 */
export default function BranchSettings({ node, dispatch }: NodeSettingsProps) {
  const instruction = (node.instruction ?? {}) as { type?: string; text?: string };

  return (
    <div className="space-y-3">
      <Field
        label="Routing question"
        hint="What the classifier reads to choose among the prompt-based transitions above. Leave blank if every edge is an equation — then no model call happens at all."
      >
        <Textarea
          rows={3}
          value={instruction.text ?? ""}
          onChange={(e) =>
            dispatch({
              type: "patchNode",
              nodeId: node.id,
              patch: { instruction: { ...instruction, type: "prompt", text: e.target.value } },
            })
          }
        />
      </Field>
      <p className="text-xs text-faint">
        A branch node never speaks. Routing by prompt costs one extra model call to classify the
        caller&rsquo;s last turn; routing purely by equations costs none — they are checked first,
        in order, before any prompt edge is considered.
      </p>
    </div>
  );
}
