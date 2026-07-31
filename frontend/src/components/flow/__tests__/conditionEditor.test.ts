import { describe, expect, test } from "bun:test";
import { hasContent } from "../settings/ConditionEditor";
import type { TransitionCondition } from "../flowModel";

/**
 * `hasContent` is the confirmation guard in front of the one destructive edit
 * in the flow editor: flipping the prompt/equation toggle calls
 * `onChange(emptyCondition(next))`, which replaces the condition object
 * outright with no undo. Anything it wrongly reports as empty is silently
 * destroyed on the first click.
 */
describe("hasContent", () => {
  const condition = (value: Record<string, unknown>) => value as TransitionCondition;

  test("an empty prompt condition is empty", () => {
    expect(hasContent(condition({ type: "prompt", prompt: "" }))).toBe(false);
    expect(hasContent(condition({ type: "prompt", prompt: "   " }))).toBe(false);
  });

  test("a written prompt condition has content", () => {
    expect(hasContent(condition({ type: "prompt", prompt: "Caller says yes" }))).toBe(true);
  });

  test("a seeded-but-blank equation is empty", () => {
    expect(
      hasContent(
        condition({
          type: "equation",
          operator: "&&",
          equations: [{ left: "", operator: "==", right: "" }],
        }),
      ),
    ).toBe(false);
  });

  test("an equation with either side filled in has content", () => {
    expect(
      hasContent(
        condition({
          type: "equation",
          operator: "&&",
          equations: [{ left: "{{status}}", operator: "==", right: "" }],
        }),
      ),
    ).toBe(true);
  });

  test("a Retell condition type this editor does not model has content", () => {
    // The regression: the toggle renders anything non-"equation" as "prompt",
    // so this used to be judged by `condition.prompt` — absent here — report
    // "empty", skip the confirmation, and destroy the condition on one click.
    expect(
      hasContent(condition({ type: "something_retell_ships_next", threshold: 0.8 })),
    ).toBe(true);
  });

  test("an unmodelled type carrying nothing but its type is still empty", () => {
    expect(hasContent(condition({ type: "something_retell_ships_next" }))).toBe(false);
  });

  test("a condition with no type at all is judged as a prompt", () => {
    expect(hasContent(condition({}))).toBe(false);
    expect(hasContent(condition({ prompt: "Caller says yes" }))).toBe(true);
  });
});
