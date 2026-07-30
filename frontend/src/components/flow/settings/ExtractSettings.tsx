"use client";

import { Plus, Trash2 } from "lucide-react";
import { Field, TextInput, Textarea } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import type { NodeSettingsProps } from "./NodeSettings";

/**
 * One entry of `node.variables`. Shape must match
 * `worker/src/arhiteq_worker/tools.py:extract_variable_parameters`, which
 * reads `{name, type, description, choices?, examples?}` — `examples` has no
 * control here, but a spread patch (never a full replace) keeps it and any
 * other unmodelled key intact per the fidelity rule.
 */
type ExtractVariable = {
  name?: string;
  type?: string;
  description?: string;
  choices?: string[];
} & Record<string, unknown>;

const TYPE_OPTIONS = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / No" },
  { value: "enum", label: "One of a list" },
];

export default function ExtractSettings({ node, dispatch }: NodeSettingsProps) {
  const variables = Array.isArray(node.variables) ? (node.variables as ExtractVariable[]) : [];

  const setVariables = (next: ExtractVariable[]) =>
    dispatch({ type: "patchNode", nodeId: node.id, patch: { variables: next } });

  const patchVariable = (index: number, patch: Partial<ExtractVariable>) => {
    setVariables(variables.map((v, i) => (i === index ? { ...v, ...patch } : v)));
  };

  return (
    <div className="space-y-3">
      <p className="text-[13px] font-medium text-ink">Variables to extract</p>
      <p className="-mt-2 text-xs text-sub">
        The model fills these in from the conversation; each becomes a {"{{variable}}"} usable
        anywhere later in the flow.
      </p>

      {variables.map((variable, index) => {
        const type = typeof variable.type === "string" ? variable.type : "string";
        // Keep an unrecognized type (e.g. authored outside this editor)
        // selectable and visible, same reasoning as EquationBuilder's
        // operator handling: never silently snap a <select> to the first
        // option while the underlying data stays untouched.
        const typeOptions = TYPE_OPTIONS.some((opt) => opt.value === type)
          ? TYPE_OPTIONS
          : [{ value: type, label: type }, ...TYPE_OPTIONS];

        return (
          // Index key, deliberately: like an equation row, a Retell variable
          // spec is `{name, type, ...}` with no id of its own, and every row
          // here is a fully controlled, stateless leaf.
          <div key={index} className="space-y-2 rounded-lg border border-line p-2.5">
            <div className="flex items-center gap-1.5">
              <TextInput
                value={variable.name ?? ""}
                onChange={(e) => patchVariable(index, { name: e.target.value })}
                placeholder="variable_name"
                className="min-w-0 grow"
              />
              <Select
                value={type}
                onChange={(v) => patchVariable(index, { type: v })}
                options={typeOptions}
                className="w-[140px] shrink-0"
              />
              <button
                type="button"
                onClick={() => setVariables(variables.filter((_, i) => i !== index))}
                className="shrink-0 rounded p-1.5 text-faint hover:bg-app hover:text-bad cursor-pointer"
                aria-label="Remove variable"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
            <Textarea
              rows={2}
              value={variable.description ?? ""}
              onChange={(e) => patchVariable(index, { description: e.target.value })}
              placeholder="What should the model look for?"
            />
            {type === "enum" && (
              <Field label="Choices" hint="Comma-separated.">
                <TextInput
                  value={(variable.choices ?? []).join(", ")}
                  onChange={(e) =>
                    patchVariable(index, {
                      choices: e.target.value
                        .split(",")
                        .map((c) => c.trim())
                        .filter(Boolean),
                    })
                  }
                  placeholder="option one, option two"
                />
              </Field>
            )}
          </div>
        );
      })}

      <button
        type="button"
        onClick={() => setVariables([...variables, { name: "", type: "string", description: "" }])}
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> Add variable
      </button>
    </div>
  );
}
