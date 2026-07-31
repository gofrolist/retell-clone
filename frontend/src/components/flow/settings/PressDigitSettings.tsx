"use client";

import { Field, TextInput, Textarea } from "@/components/ui/Field";
import { withInstruction } from "../flowModel";
import type { NodeSettingsProps } from "./NodeSettings";

// `delay_ms` bounds from Retell's PressDigitTool schema (0..5000). The worker
// clamps to the same range in `tools.press_digit_delay_s` and falls back to
// 1000ms when the field is absent or unusable, so leaving the box empty is a
// valid authoring choice rather than a hole.
const DELAY_MS_MIN = 0;
const DELAY_MS_MAX = 5000;
const DELAY_MS_DEFAULT = 1000;

/**
 * A `press_digit` node keys DTMF into an IVR. It carries no literal digits:
 * the instruction is a `prompt` telling the model what to listen for and which
 * option to choose, and the model calls the node's `press_digit` tool with the
 * digits it heard (`make_press_digit_node_tool` in `flow.py`). So the only two
 * controls here are that prompt and the pre-press delay.
 */
export default function PressDigitSettings({ node, dispatch }: NodeSettingsProps) {
  const instruction = (node.instruction ?? {}) as { type?: string; text?: string };
  const delayMs = typeof node.delay_ms === "number" ? node.delay_ms : undefined;

  return (
    <>
      <Field
        label="What to press"
        hint="Describe the menu and which option to choose — the agent listens, then keys it in."
      >
        <Textarea
          rows={3}
          placeholder="Wait for the main menu, then press 2 for the pharmacy line."
          value={instruction.text ?? ""}
          onChange={(e) =>
            dispatch({
              type: "patchNode",
              nodeId: node.id,
              // `prompt`, not `static_text`: the model has to decide the
              // digits from what it hears. Matches `defaultsFor("press_digit")`.
              patch: {
                instruction: withInstruction(node.instruction, { text: e.target.value }, "prompt"),
              },
            })
          }
        />
      </Field>
      <Field
        label="Delay before pressing (ms)"
        hint={`IVR menus speak slowly; the agent waits this long before keying in. Default ${DELAY_MS_DEFAULT}ms.`}
        className="mt-3"
      >
        <TextInput
          type="number"
          min={DELAY_MS_MIN}
          max={DELAY_MS_MAX}
          placeholder={String(DELAY_MS_DEFAULT)}
          value={delayMs ?? ""}
          onChange={(e) => {
            const raw = e.target.value.trim();
            // An empty box clears the field entirely rather than writing 0 —
            // absent means "use the worker's default", which is not the same
            // as "press immediately".
            const next =
              raw === ""
                ? undefined
                : Math.min(Math.max(Number(raw), DELAY_MS_MIN), DELAY_MS_MAX);
            dispatch({
              type: "patchNode",
              nodeId: node.id,
              patch: { delay_ms: Number.isFinite(next as number) ? next : undefined },
            });
          }}
        />
      </Field>
    </>
  );
}
