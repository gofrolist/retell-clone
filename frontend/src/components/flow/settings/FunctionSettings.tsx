"use client";

import { Field } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Toggle from "@/components/ui/Toggle";
import type { NodeSettingsProps } from "./NodeSettings";

/**
 * `tool_id` is a flow-scoped identifier matched against `flow.tools[]`
 * (`worker/src/arhiteq_worker/flow.py:make_function_node_tool`) — unrelated
 * to the node's own id or the tool's display `name`. When it does not
 * resolve (or the resolved entry has no `url`), the worker logs a warning and
 * installs nothing for this node: it can neither act nor advance on a
 * function outcome, so the warning below is surfaced the same way.
 */
export default function FunctionSettings({ node, flow, dispatch }: NodeSettingsProps) {
  const tools = Array.isArray(flow.tools) ? (flow.tools as Record<string, unknown>[]) : [];
  const toolId = typeof node.tool_id === "string" ? node.tool_id : "";
  const resolved = tools.find((t) => t.tool_id === toolId);
  const waitForResult = node.wait_for_result !== false;
  const speaks = Boolean(node.speak_during_execution);

  const patch = (patch: Record<string, unknown>) =>
    dispatch({ type: "patchNode", nodeId: node.id, patch });

  return (
    <div className="space-y-3">
      <Field label="Tool">
        <Select
          value={toolId}
          onChange={(v) => patch({ tool_id: v })}
          options={[
            { value: "", label: "No tool selected" },
            ...tools.map((t) => ({
              value: typeof t.tool_id === "string" ? t.tool_id : "",
              label:
                typeof t.name === "string" && t.name ? t.name : String(t.tool_id ?? "Unnamed tool"),
            })),
          ]}
        />
        {toolId && !resolved && (
          <p className="mt-1.5 text-xs text-bad">
            This tool_id is not in the flow&rsquo;s tools list. The worker skips this node&rsquo;s
            action with a warning — it will not be able to act.
          </p>
        )}
      </Field>

      <Field
        label="Wait for the result"
        hint="Off fires the request and moves on immediately, without its response."
      >
        <Toggle checked={waitForResult} onChange={(v) => patch({ wait_for_result: v })} />
      </Field>

      <Field label="Speak while running" hint="Says a filler line while the tool call is in flight.">
        <Toggle checked={speaks} onChange={(v) => patch({ speak_during_execution: v })} />
      </Field>
    </div>
  );
}
