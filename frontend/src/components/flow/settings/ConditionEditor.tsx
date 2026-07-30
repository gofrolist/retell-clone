"use client";

import { useState } from "react";
import { cn } from "@/lib/utils";
import Button from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { emptyCondition, type TransitionCondition } from "../flowModel";
import EquationBuilder from "./EquationBuilder";

/** Does *condition* carry authored content a type switch would throw away? */
function hasContent(condition: TransitionCondition): boolean {
  if (condition.type === "equation") {
    const equations = Array.isArray(condition.equations) ? condition.equations : [];
    return equations.some((equation) => {
      const left = equation.left;
      const right = equation.right;
      const leftText = left == null ? "" : typeof left === "string" ? left : String(left);
      const rightText = right == null ? "" : typeof right === "string" ? right : String(right);
      return leftText.trim() !== "" || rightText.trim() !== "";
    });
  }
  // Anything that is not (yet) "equation" is treated as the "prompt" side —
  // matching the fallback `type` derivation below — so its content lives in
  // `prompt`.
  return typeof condition.prompt === "string" && condition.prompt.trim() !== "";
}

/**
 * `type: "prompt"` vs. `type: "equation"` switch plus the matching editor
 * below it: a `<Textarea>` bound to `condition.prompt`, or `EquationBuilder`.
 *
 * Flipping the switch calls `onChange(emptyCondition(next))` — a fresh,
 * worker-valid condition of the new type — but only after confirming when
 * the current condition `hasContent`: switching away from a non-empty
 * condition throws away everything the user authored, and there is no undo,
 * so a `Modal` confirms first rather than losing it silently.
 */
export default function ConditionEditor({
  condition,
  onChange,
  variables,
}: {
  condition: TransitionCondition;
  onChange: (next: TransitionCondition) => void;
  variables: string[];
}) {
  const type = condition.type === "equation" ? "equation" : "prompt";
  const [pendingType, setPendingType] = useState<"prompt" | "equation" | null>(null);

  const switchTo = (next: "prompt" | "equation") => {
    if (next === type) return;
    if (hasContent(condition)) {
      setPendingType(next);
      return;
    }
    onChange(emptyCondition(next));
  };

  const confirmSwitch = () => {
    if (pendingType) onChange(emptyCondition(pendingType));
    setPendingType(null);
  };

  return (
    <div className="space-y-3">
      <div className="inline-flex items-center gap-0.5 rounded-lg border border-line bg-app p-0.5">
        {(["prompt", "equation"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => switchTo(t)}
            className={cn(
              "rounded-md px-3 py-1.5 text-[13px] font-medium capitalize transition-colors cursor-pointer",
              type === t
                ? "border border-line bg-white text-ink shadow-sm"
                : "text-sub hover:text-ink",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {type === "prompt" ? (
        <Textarea
          value={typeof condition.prompt === "string" ? condition.prompt : ""}
          onChange={(e) => onChange({ ...condition, type: "prompt", prompt: e.target.value })}
          placeholder="Describe when this transition should fire, in plain language."
          rows={3}
        />
      ) : (
        <EquationBuilder condition={condition} onChange={onChange} variables={variables} />
      )}

      <Modal
        open={pendingType !== null}
        onClose={() => setPendingType(null)}
        title="Discard this condition?"
        footer={
          <>
            <Button variant="secondary" onClick={() => setPendingType(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmSwitch}>
              Discard and switch
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-sub">
          Switching to {pendingType === "equation" ? "Equation" : "Prompt"} discards the{" "}
          {type === "equation" ? "equation" : "prompt"} you have written for this transition.
        </p>
      </Modal>
    </div>
  );
}
