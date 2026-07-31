"use client";

import LlmModelSelect from "@/components/editor/LlmModelSelect";
import { Field, Textarea } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Slider from "@/components/ui/Slider";
import type { RawConversationFlow } from "@/lib/api";
import type { FlowAction } from "../flowModel";

const START_SPEAKER_OPTIONS = [
  { value: "agent", label: "AI speaks first" },
  { value: "user", label: "User speaks first" },
];

/**
 * Flow-level settings: everything that lives on the conversation flow itself
 * rather than on any one node — `global_prompt`, `start_speaker`,
 * `model_choice.model`, `model_temperature`. Mounted by `FlowEditor` as a
 * tab beside `NodeSettings` (see the tab switcher there), same
 * `{flow, dispatch, readOnly}` shape as `NodeSettings` itself takes.
 */
export default function GlobalSettings({
  flow,
  dispatch,
  readOnly,
}: {
  flow: RawConversationFlow;
  dispatch: (action: FlowAction) => void;
  readOnly: boolean;
}) {
  const patchFlow = (patch: Record<string, unknown>) => dispatch({ type: "patchFlow", patch });

  // Cast, don't reconstruct: `model_choice` is `Record<string, unknown>` on
  // the wire (may carry keys this pane doesn't show, e.g. a Retell-imported
  // flow's own extras) — reading two known fields off it must not lose the
  // rest when patched back with a spread below.
  const modelChoice = (flow.model_choice ?? {}) as { type?: string; model?: string };
  const modelValue = typeof modelChoice.model === "string" ? modelChoice.model : "";
  const startSpeaker = flow.start_speaker === "user" ? "user" : "agent";
  const temperature = typeof flow.model_temperature === "number" ? flow.model_temperature : 0;

  return (
    <fieldset disabled={readOnly} className="min-w-0 space-y-4 p-4">
      <div>
        <p className="text-[13px] font-medium text-ink">Global Settings</p>
        <p className="text-xs text-sub">Apply to the whole flow, not to any single node.</p>
      </div>

      <Field
        label="Global prompt"
        hint="Prepended to every node's instructions — persona, tone, and rules that should hold everywhere. A node's own instruction only covers that node's step."
      >
        <Textarea
          rows={6}
          value={typeof flow.global_prompt === "string" ? flow.global_prompt : ""}
          onChange={(e) => patchFlow({ global_prompt: e.target.value })}
          placeholder="e.g. You are a support agent for Acme. Always be concise and polite."
        />
      </Field>

      <Field
        label="Who speaks first"
        hint="Who speaks first the moment the call connects. A conversation node's own “Who speaks first” setting, when set, overrides this for that node only."
      >
        <Select
          value={startSpeaker}
          onChange={(v) => patchFlow({ start_speaker: v === "user" ? "user" : "agent" })}
          className="w-full"
          options={START_SPEAKER_OPTIONS}
        />
      </Field>

      <Field
        label="Model"
        hint="Arhiteq runs conversation on Gemini only. A non-Gemini model name here — e.g. a flow imported from Retell naming gpt-5.1 — is mapped onto the deployment's default Gemini model at call time; it is not used as-is."
      >
        <LlmModelSelect
          value={modelValue}
          onChange={(v) => patchFlow({ model_choice: { ...modelChoice, model: v } })}
          className="w-full"
        />
      </Field>

      <Field label="Model temperature" hint="Lower value yields better function call results.">
        <Slider
          min={0}
          max={1}
          step={0.01}
          value={temperature}
          onChange={(v) => patchFlow({ model_temperature: v })}
          format={(v) => v.toFixed(2)}
        />
      </Field>
    </fieldset>
  );
}
