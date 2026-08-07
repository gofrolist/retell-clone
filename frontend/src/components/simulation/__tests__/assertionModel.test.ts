import { describe, expect, test } from "bun:test";
import {
  argumentPatterns,
  assertionKind,
  assertionProblem,
  assertionsProblem,
  blankAssertion,
  changeKind,
  formatScript,
  normalizeAssertions,
  parseScript,
  toEditableAssertions,
  withExtra,
  withValue,
  type Assertion,
} from "../assertionModel";

/**
 * The backend reads an assertion's type from its FIRST key
 * (`services/assertions.py`: `next(iter(assertion))`). An assertion whose keys
 * come back in another order is not a formatting difference — it is read as a
 * different type, or as no known type at all, and fails with "Unknown assertion
 * type 'with'". Nothing else in the editor can catch that, because both objects
 * carry exactly the same entries.
 */
describe("the type key stays first", () => {
  const firstKey = (a: Assertion) => Object.keys(a)[0];

  test("a new assertion leads with its type", () => {
    expect(firstKey(blankAssertion("tool_called"))).toBe("tool_called");
    expect(firstKey(blankAssertion("turn_count_max"))).toBe("turn_count_max");
  });

  test("editing the value does not move the type behind its extras", () => {
    const withExtras: Assertion = { tool_called: "log_mood", with: { phone: "^\\+1" }, times: 1 };
    expect(firstKey(withValue(withExtras, "log_outcome"))).toBe("tool_called");
  });

  test("a value edit keeps the extras it was not asked to touch", () => {
    const edited = withValue({ tool_called: "log_mood", with: { phone: "^\\+1" }, times: 1 }, "x");
    expect(edited).toEqual({ tool_called: "x", with: { phone: "^\\+1" }, times: 1 });
  });

  test("an assertion whose keys arrive out of order is not reinterpreted", () => {
    // Written by hand through the API, this already fails server-side with
    // "Unknown assertion type 'with'". The editor identifies a type exactly the
    // way the backend does — first key, no search — so it reports the row as
    // one it does not model instead of guessing which key was meant to lead.
    // Guessing would hide a case that is already broken.
    const wrong: Assertion = { with: { phone: "^\\+1" }, tool_called: "log_mood" };
    expect(assertionKind(wrong)).toBeNull();
    expect(withValue(wrong, "log_mood")).toBe(wrong);
  });

  test("adding an extra leaves the type in front", () => {
    const next = withExtra(blankAssertion("tool_called"), "times", "2");
    expect(firstKey(next)).toBe("tool_called");
  });

  test("normalizing puts the type first however the row was built", () => {
    const wrong: Assertion = { times: "1", tool_called: "log_mood" };
    // `assertionKind` reads `times` here, so normalize leaves it alone rather
    // than guessing — the read-only path. What matters is that it is not
    // silently rewritten into a broken `tool_called`.
    expect(normalizeAssertions([wrong])[0]).toEqual(wrong);
  });
});

describe("assertionKind", () => {
  test("reads the leading key", () => {
    expect(assertionKind({ said_never: "\\{\\{" })).toBe("said_never");
  });

  test("a type this editor does not model is null, not a guess", () => {
    // Types have been specced and not built before (`swapped_to`, `no_reask`).
    // If one ships server-side first, the editor must leave the row alone
    // rather than rewrite it into the nearest thing it understands.
    expect(assertionKind({ swapped_to: "clara-pharmacy" })).toBeNull();
  });

  test("an empty object has no type", () => {
    expect(assertionKind({})).toBeNull();
  });
});

describe("changeKind", () => {
  test("keeps the function name between two function-named types", () => {
    expect(changeKind({ tool_called: "to_billing" }, "tool_not_called")).toEqual({
      tool_not_called: "to_billing",
    });
  });

  test("keeps the pattern between the two pattern types", () => {
    expect(changeKind({ said_matches: "(?i)doctor" }, "said_never")).toEqual({
      said_never: "(?i)doctor",
    });
  });

  test("does not carry a pattern into a slot that wants a function", () => {
    // `{tool_called: "(?i)doctor"}` reads as deliberate in review and can never
    // pass, because no function is named that.
    expect(changeKind({ said_matches: "(?i)doctor" }, "tool_called")).toEqual({ tool_called: "" });
  });

  test("drops the extras when leaving tool_called", () => {
    const from: Assertion = { tool_called: "log_mood", with: { phone: "^1" }, times: 1 };
    expect(changeKind(from, "tool_not_called")).toEqual({ tool_not_called: "log_mood" });
  });

  test("retyping to the same type is a no-op", () => {
    const same: Assertion = { tool_called: "log_mood", times: 2 };
    expect(changeKind(same, "tool_called")).toBe(same);
  });
});

describe("assertionProblem", () => {
  test("a complete assertion has no problem", () => {
    expect(assertionProblem({ tool_called: "log_mood" })).toBeNull();
    expect(assertionProblem({ tool_order: ["log_outcome", "end_call"] })).toBeNull();
  });

  test("a function-named type needs a function", () => {
    expect(assertionProblem({ tool_called: "  " })).toMatch(/function name/);
  });

  test("a pattern wrapped in slashes is refused", () => {
    // This is the assertion that passes forever while asserting nothing: Python
    // matches the slashes literally, so the pattern never matches and
    // `said_never` is green on every run.
    expect(assertionProblem({ said_never: "/transfer you/i" })).toMatch(/not as \/pattern\/flags/);
    expect(assertionProblem({ tool_called: "x", with: { to: "/^\\+1/" } })).toMatch(
      /not as \/pattern\/flags/,
    );
  });

  test("a pattern that merely contains a slash is fine", () => {
    expect(assertionProblem({ said_matches: "9/11" })).toBeNull();
    expect(assertionProblem({ said_matches: "(?i)https?://" })).toBeNull();
  });

  test("exactly 0 times is refused with the fix", () => {
    // The backend's never-called guard fires before `times` is read, so this
    // can only ever fail — and it is the obvious way to try to say "never".
    expect(assertionProblem({ tool_called: "to_billing", times: 0 })).toMatch(/Never called/);
  });

  test("a non-integer count is refused", () => {
    expect(assertionProblem({ tool_called: "log_mood", times: "twice" })).toMatch(/whole number/);
    expect(assertionProblem({ tool_called: "log_mood", times: "1.5" })).toMatch(/whole number/);
  });

  test("an absent count is not a count of nothing", () => {
    expect(assertionProblem({ tool_called: "log_mood" })).toBeNull();
    expect(assertionProblem({ tool_called: "log_mood", times: "" })).toBeNull();
  });

  test("an order of one is not an order", () => {
    expect(assertionProblem({ tool_order: ["end_call"] })).toMatch(/at least two/);
  });

  test("turn_count_max needs a positive whole number", () => {
    expect(assertionProblem({ turn_count_max: "0" })).toMatch(/at least 1/);
    expect(assertionProblem({ turn_count_max: "" })).toMatch(/whole number/);
    expect(assertionProblem({ turn_count_max: "8" })).toBeNull();
  });

  test("an unmodelled type is left alone rather than failed", () => {
    expect(assertionProblem({ swapped_to: "clara-pharmacy" })).toBeNull();
  });

  test("the list reports which row is wrong", () => {
    const problem = assertionsProblem([{ tool_called: "log_mood" }, { said_never: "" }]);
    expect(problem).toMatch(/^Assertion 2:/);
  });

  test("a clean list has no problem", () => {
    expect(assertionsProblem([{ tool_called: "log_mood" }])).toBeNull();
  });
});

describe("normalizeAssertions", () => {
  test("counts typed as text become numbers", () => {
    // They are held as text while editing so a half-typed value is not
    // reinterpreted under the cursor; the wire wants numbers.
    expect(normalizeAssertions([{ turn_count_max: "8" }])).toEqual([{ turn_count_max: 8 }]);
    expect(normalizeAssertions([{ tool_called: "x", times: "2" }])).toEqual([
      { tool_called: "x", times: 2 },
    ]);
  });

  test("an empty count is dropped, not sent as zero", () => {
    // `times: 0` is an assertion that can never pass, so an untouched count
    // field must not become one.
    expect(normalizeAssertions([{ tool_called: "x", times: "" }])).toEqual([{ tool_called: "x" }]);
  });

  test("an abandoned argument row is dropped", () => {
    expect(normalizeAssertions([{ tool_called: "x", with: { "": "^1" } }])).toEqual([
      { tool_called: "x" },
    ]);
  });

  test("an empty argument map is dropped rather than sent", () => {
    expect(normalizeAssertions([{ tool_called: "x", with: {} }])).toEqual([{ tool_called: "x" }]);
  });

  test("blank steps in an order are dropped", () => {
    expect(normalizeAssertions([{ tool_order: ["a", "  ", "b"] }])).toEqual([
      { tool_order: ["a", "b"] },
    ]);
  });

  test("values are trimmed", () => {
    expect(normalizeAssertions([{ said_never: "  \\{\\{  " }])).toEqual([{ said_never: "\\{\\{" }]);
  });

  test("an unmodelled type passes through untouched", () => {
    const exotic: Assertion = { swapped_to: "clara-pharmacy", extra: 1 };
    expect(normalizeAssertions([exotic])[0]).toEqual(exotic);
  });
});

/**
 * `with` is a map on the wire and a list while editing. The conversion exists
 * for one reason: a map keyed by argument name cannot hold two rows whose names
 * are both still blank, so a second "Add argument" would replace the first and
 * a pattern typed before its name would vanish under the cursor.
 */
describe("argument patterns", () => {
  test("two blank rows both survive editing", () => {
    const editing: Assertion = {
      tool_called: "send_resource_sms",
      with: [
        { key: "", value: "" },
        { key: "", value: "" },
      ],
    };
    expect(argumentPatterns(editing)).toHaveLength(2);
  });

  test("a saved case opens with its patterns as rows", () => {
    const [editable] = toEditableAssertions([
      { tool_called: "log_medication_taken", with: { medication_name: "(?i)lipitor" }, times: 1 },
    ]);
    expect(editable).toEqual({
      tool_called: "log_medication_taken",
      with: [{ key: "medication_name", value: "(?i)lipitor" }],
      times: "1",
    });
  });

  test("a count opens as text so a half-typed value is not reinterpreted", () => {
    expect(toEditableAssertions([{ turn_count_max: 8 }])).toEqual([{ turn_count_max: "8" }]);
  });

  test("rows collapse back to a map on save", () => {
    const editing: Assertion = {
      tool_called: "send_resource_sms",
      with: [
        { key: "to_number", value: "^\\+1" },
        { key: "", value: "abandoned" },
      ],
    };
    expect(normalizeAssertions([editing])).toEqual([
      { tool_called: "send_resource_sms", with: { to_number: "^\\+1" } },
    ]);
  });

  test("a saved case round trips unchanged", () => {
    // Opening a case and saving it without touching anything must not rewrite
    // what it asserts — the diff between two runs would then be the editor.
    const stored: Assertion[] = [
      { tool_called: "log_mood", with: { phone: "^\\+1" }, times: 1 },
      { tool_order: ["log_outcome", "end_call"] },
      { said_never: "\\{\\{" },
      { turn_count_max: 8 },
    ];
    expect(normalizeAssertions(toEditableAssertions(stored))).toEqual(stored);
  });

  test("an abandoned row does not block a save", () => {
    const editing: Assertion = {
      tool_called: "log_mood",
      with: [{ key: "", value: "" }],
    };
    expect(assertionProblem(editing)).toBeNull();
  });

  test("a pattern typed with no argument name is refused", () => {
    const editing: Assertion = {
      tool_called: "log_mood",
      with: [{ key: "", value: "(?i)lipitor" }],
    };
    expect(assertionProblem(editing)).toMatch(/missing its argument name/);
  });

  test("an unmodelled type survives the round trip", () => {
    const exotic: Assertion = { swapped_to: "clara-pharmacy" };
    expect(normalizeAssertions(toEditableAssertions([exotic]))).toEqual([exotic]);
  });
});

describe("the script", () => {
  test("one caller turn per line", () => {
    expect(parseScript("Morning Clara.\nI took the Lipitor.")).toEqual([
      "Morning Clara.",
      "I took the Lipitor.",
    ]);
  });

  test("blank lines are dropped", () => {
    // A pasted transcript arrives with them, and an empty caller turn would be
    // spoken as silence and shift every later turn against its assertions.
    expect(parseScript("Morning.\n\n  \nThat's everything.")).toEqual([
      "Morning.",
      "That's everything.",
    ]);
  });

  test("nothing typed is no script at all", () => {
    // Not one empty turn: `script.length > 0` is what makes a case scripted.
    expect(parseScript("   \n  ")).toEqual([]);
  });

  test("round trips through the editor", () => {
    const script = ["Morning Clara.", "No, that's everything."];
    expect(parseScript(formatScript(script))).toEqual(script);
  });

  test("a case with no script formats to an empty box", () => {
    expect(formatScript(undefined)).toBe("");
  });
});
