"use client";

import { cn } from "@/lib/utils";
import { Plus, Trash2 } from "lucide-react";
import Select from "@/components/ui/Select";
import { TextInput } from "@/components/ui/Field";
import { EQUATION_OPERATORS, type Equation, type TransitionCondition } from "../flowModel";

const OPERATOR_OPTIONS = EQUATION_OPERATORS.map((op) => ({ value: op, label: op }));

function operandText(value: unknown): string {
  if (value == null) return "";
  return typeof value === "string" ? value : String(value);
}

/**
 * One operand: a free-text input (the actual bound value — a literal or a
 * hand-typed `{{variable}}`) plus a `<Select>` of known variables next to it.
 * Picking a variable just writes `{{name}}` into the text input; the select
 * itself never holds state, so this is a picker, not a second source of
 * truth. There is no combobox in the UI kit, so this pairing (rather than
 * inventing one) is deliberate — see the Task 5 brief.
 */
function OperandRow({
  value,
  onChange,
  variables,
  placeholder,
}: {
  value: unknown;
  onChange: (next: string) => void;
  variables: string[];
  placeholder: string;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <TextInput
        value={operandText(value)}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 grow"
      />
      <Select
        value=""
        onChange={(name) => name && onChange(`{{${name}}}`)}
        options={[
          { value: "", label: "Insert var…" },
          ...variables.map((name) => ({ value: name, label: `{{${name}}}` })),
        ]}
        className="w-[124px] shrink-0"
      />
    </div>
  );
}

/**
 * One row per `{left, operator, right}` equation of an `equation`
 * `transition_condition`, an `&&`/`||` join toggle once there are two or
 * more rows, and the evaluation-order hint — the worker
 * (`evaluate_equation_condition` in `worker/src/arhiteq_worker/flow.py`)
 * checks these in order, before the model is ever consulted, and the first
 * edge whose condition is true wins. Nothing else in the UI surfaces that,
 * so it is required copy, not decoration.
 *
 * `right` is hidden for the unary `exists` operator: the worker ignores it
 * entirely for that operator (`_evaluate_single_equation`), so showing an
 * input the worker never reads would just mislead the author.
 */
export default function EquationBuilder({
  condition,
  onChange,
  variables,
}: {
  condition: TransitionCondition;
  onChange: (next: TransitionCondition) => void;
  variables: string[];
}) {
  const equations = Array.isArray(condition.equations) ? condition.equations : [];
  const joinOperator = condition.operator === "||" ? "||" : "&&";

  const setEquations = (next: Equation[]) => {
    // Never let the list go empty: the worker's `evaluate_equation_condition`
    // treats an empty `equations` array as an unconditional `False`, so a
    // condition with none would silently never fire.
    onChange({
      ...condition,
      equations: next.length > 0 ? next : [{ left: "", operator: "==", right: "" }],
    });
  };

  const patchEquation = (index: number, patch: Partial<Equation>) => {
    setEquations(equations.map((eq, i) => (i === index ? { ...eq, ...patch } : eq)));
  };

  return (
    <div className="space-y-2">
      {equations.map((equation, index) => {
        const operator = typeof equation.operator === "string" ? equation.operator : "==";
        // Keep an unrecognized operator (real data the worker doesn't
        // implement, or a value authored outside this editor) selectable and
        // visible rather than silently snapping the <select> to the first
        // option while the underlying data stays untouched.
        const operatorOptions = OPERATOR_OPTIONS.some((o) => o.value === operator)
          ? OPERATOR_OPTIONS
          : [{ value: operator, label: operator }, ...OPERATOR_OPTIONS];

        return (
          <div key={index} className="space-y-1.5 rounded-lg border border-line p-2.5">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-medium tracking-wide text-faint uppercase">
                {index === 0 ? "If" : joinOperator === "&&" ? "And" : "Or"}
              </span>
              <button
                type="button"
                onClick={() => setEquations(equations.filter((_, i) => i !== index))}
                className="rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
                aria-label="Remove condition"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
            <OperandRow
              value={equation.left}
              onChange={(v) => patchEquation(index, { left: v })}
              variables={variables}
              placeholder="Variable or value"
            />
            <Select
              value={operator}
              onChange={(v) => patchEquation(index, { operator: v })}
              options={operatorOptions}
            />
            {operator !== "exists" && (
              <OperandRow
                value={equation.right}
                onChange={(v) => patchEquation(index, { right: v })}
                variables={variables}
                placeholder="Value to compare"
              />
            )}
          </div>
        );
      })}

      <button
        type="button"
        onClick={() => setEquations([...equations, { left: "", operator: "==", right: "" }])}
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> Add condition
      </button>

      {equations.length >= 2 && (
        <div className="flex items-center gap-2">
          <span className="text-[13px] text-sub">Combine with</span>
          <div className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-app p-0.5">
            {(["&&", "||"] as const).map((op) => (
              <button
                key={op}
                type="button"
                onClick={() => onChange({ ...condition, operator: op })}
                className={cn(
                  "rounded-md px-2.5 py-1 text-[12px] font-medium transition-colors cursor-pointer",
                  joinOperator === op
                    ? "border border-line bg-white text-ink shadow-sm"
                    : "text-sub hover:text-ink",
                )}
              >
                {op === "&&" ? "AND" : "OR"}
              </button>
            ))}
          </div>
        </div>
      )}

      <p className="text-xs text-faint">
        Equations are checked in order before the model is asked anything. The first edge whose
        condition is true wins.
      </p>
    </div>
  );
}
