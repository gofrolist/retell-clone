import { describe, expect, test } from "bun:test";
import type { RawTestCase, ToolMock } from "@/lib/api";
import {
  UNKNOWN_AGENT,
  agentSlug,
  caseFileName,
  toCaseFile,
  whyNotExportable,
} from "../caseExport";

/** Evaluate the emitted source the way `require` would, and hand back what it
 *  exported. Parsing rather than string-matching is the point: this module
 *  exists so the sync can `require` what it writes, and only running it says so. */
function evaluated(source: string): Record<string, unknown> {
  const target = { exports: {} as Record<string, unknown> };
  new Function("module", source)(target);
  return target.exports;
}

const testCase = (over: Partial<RawTestCase> = {}): RawTestCase => ({
  test_case_definition_id: "tcd_1",
  type: "simulation",
  name: "M01 morning dose is logged once the caller confirms it",
  user_prompt: "",
  metrics: [],
  dynamic_variables: { first_name: "Margaret", current_time: "2026-08-07T08:15" },
  tool_mocks: [
    { tool_name: "log_medication_taken", input_match_rule: { type: "any" }, output: "{}" },
  ],
  script: ["Morning Clara.", "I took the Lipitor."],
  assertions: [{ tool_called: "log_medication_taken", with: { medication_name: "(?i)lipitor" } }],
  creation_timestamp: 1,
  user_modified_timestamp: 1,
  ...over,
});

/**
 * Refusing beats exporting something lossy. `translate.js` reads only `name`,
 * `agent`, `variables`, `tools`, `script` and `assert` — anything else written
 * into a case file is dropped in silence at the next sync, and the case comes
 * back graded by less than it was.
 */
describe("what can be exported", () => {
  test("a scripted case with assertions can be", () => {
    expect(whyNotExportable(testCase())).toBeNull();
  });

  test("an improvised case cannot", () => {
    expect(whyNotExportable(testCase({ script: [] }))).toMatch(/Only a scripted case/);
  });

  test("a scripted case that asserts nothing cannot", () => {
    // It would export as a conversation with no verdict in it — a file that
    // syncs, runs, costs a model call and reports nothing.
    expect(whyNotExportable(testCase({ assertions: [] }))).toMatch(/at least one assertion/);
  });

  test("a case carrying a judged criterion cannot", () => {
    // The criterion has nowhere to go in the file format, so exporting would
    // quietly weaken the case.
    expect(whyNotExportable(testCase({ metrics: ["Clara asks a follow-up"] }))).toMatch(
      /no field for a judged criterion/,
    );
  });
});

describe("the filename", () => {
  test("reads like the case it came from", () => {
    expect(caseFileName("M01 morning dose is logged once the caller confirms it")).toBe(
      "m01-morning-dose-is-logged-once.case.js",
    );
  });

  test("survives punctuation and repeated separators", () => {
    expect(caseFileName("P02 — pharmacy won't text!!")).toBe("p02-pharmacy-won-t-text.case.js");
  });

  test("a name with nothing usable in it still gets a filename", () => {
    expect(caseFileName("???")).toBe("case.case.js");
  });
});

describe("the agent slug", () => {
  test("a name that is already a slug is left alone", () => {
    expect(agentSlug("clara-checkin")).toBe("clara-checkin");
  });

  test("a display name becomes one", () => {
    expect(agentSlug("Clara Check-in")).toBe("clara-check-in");
  });

  test("an unnamed agent produces a key no sync can resolve", () => {
    // Not a real key. `clara-checkin` would resolve, and the case would go on
    // to grade Clara's prompt under a name nobody chose — the opposite of
    // failing loudly.
    expect(agentSlug("  ")).toBe(UNKNOWN_AGENT);
    expect(UNKNOWN_AGENT).not.toMatch(/^clara-/);
  });
});

describe("the emitted file", () => {
  test("is valid JavaScript that exports the case", () => {
    // Parsed rather than string-matched: the point of this module is that what
    // it writes can be `require`d by the sync, and only evaluating it says so.
    const source = toCaseFile(testCase(), { agentName: "clara-checkin" });
    const exported = evaluated(source);
    expect(exported.name).toBe("M01 morning dose is logged once the caller confirms it");
    expect(exported.agent).toBe("clara-checkin");
    expect(exported.script).toEqual(["Morning Clara.", "I took the Lipitor."]);
    expect(exported.assert).toEqual([
      { tool_called: "log_medication_taken", with: { medication_name: "(?i)lipitor" } },
    ]);
  });

  test("an assertion keeps its type as the leading key", () => {
    // The engine reads an assertion's type from its first key. A file that
    // emitted `with` first would sync back as an unknown assertion type.
    const source = toCaseFile(testCase(), { agentName: "clara-checkin" });
    const exported = evaluated(source);
    const [first] = exported.assert as Record<string, unknown>[];
    expect(Object.keys(first)[0]).toBe("tool_called");
  });

  test("the pinned clock folds back to {{today}}", () => {
    // Left as a real date, the case grades that one calendar day forever and
    // the morning/evening branches stop moving with it.
    const source = toCaseFile(testCase(), { agentName: "clara-checkin" });
    const exported = evaluated(source);
    expect((exported.variables as Record<string, string>).current_time).toBe("{{today}}T08:15");
  });

  test("a clock pinned on some earlier day folds too", () => {
    // The regression: a case synced yesterday stores yesterday's date, and a
    // fold that only recognised *today* would hard-code the clock on every
    // export after the day the case was written — the exact failure the fold
    // exists to prevent, arrived at one day later.
    const source = toCaseFile(
      testCase({ dynamic_variables: { current_time: "2025-12-30T19:05" } }),
      { agentName: "clara-checkin" },
    );
    const exported = evaluated(source);
    expect((exported.variables as Record<string, string>).current_time).toBe("{{today}}T19:05");
  });

  test("a date in any other variable means that date and is left alone", () => {
    // A trial start or a last-assessment stamp is not the clock — rewriting it
    // would change what the case tests. `validate.js` singles out
    // `current_time` by name for the same reason.
    const source = toCaseFile(testCase({ dynamic_variables: { trial_started: "2026-01-15" } }), {
      agentName: "clara-checkin",
    });
    const exported = evaluated(source);
    expect((exported.variables as Record<string, string>).trial_started).toBe("2026-01-15");
  });

  test("a mock payload comes back as an object, the way a case file writes one", () => {
    const source = toCaseFile(
      testCase({
        tool_mocks: [
          {
            tool_name: "recall_lead_context",
            input_match_rule: { type: "any" },
            output: '{"first_name": "Margaret"}',
          },
        ],
      }),
      { agentName: "clara-checkin" },
    );
    const exported = evaluated(source);
    expect(exported.tools).toEqual({ recall_lead_context: { first_name: "Margaret" } });
  });

  test("a mock payload that is not JSON stays the string it was", () => {
    const source = toCaseFile(
      testCase({
        tool_mocks: [
          { tool_name: "kb_lookup", input_match_rule: { type: "any" }, output: "not json at all" },
        ],
      }),
      { agentName: "clara-checkin" },
    );
    const exported = evaluated(source);
    expect(exported.tools).toEqual({ kb_lookup: "not json at all" });
  });

  test("the same case exported twice produces the same file", () => {
    // `today` is an argument and not the clock, so a re-export diffs only where
    // the case actually changed.
    const once = toCaseFile(testCase(), { agentName: "clara-checkin" });
    const twice = toCaseFile(testCase(), { agentName: "clara-checkin" });
    expect(once).toBe(twice);
  });

  test("a name with a quote in it does not break the file", () => {
    const source = toCaseFile(testCase({ name: 'C01 the caller says "not today"' }), {
      agentName: "clara-checkin",
    });
    const exported = evaluated(source);
    expect(exported.name).toBe('C01 the caller says "not today"');
  });
});

/**
 * The emitted file is `require`d by the sync and by `run.js`. Anything from the
 * case that escapes its literal is arbitrary code running on whoever runs the
 * suite — and a case name is written by anyone with access to the workspace.
 */
describe("nothing in the case can escape into code", () => {
  test("a name carrying a comment terminator cannot break out of the header", () => {
    // The regression, and it ran: `/** ${name}` closed early on `*/`, executed
    // what followed, and the trailing `/*` swallowed the rest of the header so
    // the file still parsed and looked ordinary.
    const hostile = 'M01 */ globalThis.ESCAPED = 1; /*';
    const source = toCaseFile(testCase({ name: hostile }), { agentName: "clara-checkin" });
    const exported = evaluated(source);
    expect((globalThis as Record<string, unknown>).ESCAPED).toBeUndefined();
    expect(exported.name).toBe(hostile);
  });

  test("a script line carrying one cannot either", () => {
    const source = toCaseFile(
      testCase({ script: ["*/ globalThis.ESCAPED_SCRIPT = 1; /*"] }),
      { agentName: "clara-checkin" },
    );
    evaluated(source);
    expect((globalThis as Record<string, unknown>).ESCAPED_SCRIPT).toBeUndefined();
  });

  test("a backtick and a template expression are inert", () => {
    // The file is built with a template literal, so an unescaped value carrying
    // one would be evaluated at export time rather than at require time.
    const name = "M01 `${globalThis.ESCAPED_TEMPLATE = 1}`";
    const exported = evaluated(toCaseFile(testCase({ name }), { agentName: "clara-checkin" }));
    expect((globalThis as Record<string, unknown>).ESCAPED_TEMPLATE).toBeUndefined();
    expect(exported.name).toBe(name);
  });
});

/**
 * A case file has one payload per function and no `input_match_rule`. Both
 * shapes below survive the trip only by changing what the case grades, so the
 * export refuses them rather than producing a file that syncs and lies.
 */
describe("mocks a case file cannot express", () => {
  const mock = (tool_name: string, rule: ToolMock["input_match_rule"], output = "{}") => ({
    tool_name,
    input_match_rule: rule,
    output,
  });

  test("two mocks for one function are refused", () => {
    // A map keeps the LAST; `match_tool_mock` uses the FIRST. So exporting
    // would not merely lose a mock, it would keep the opposite one — and both
    // rows are two clicks apart in the modal.
    const problem = whyNotExportable(
      testCase({
        tool_mocks: [
          mock("log_medication_taken", { type: "any" }, '{"ok": true}'),
          mock("log_medication_taken", { type: "any" }, '{"ok": false}'),
        ],
      }),
    );
    expect(problem).toMatch(/Two mocks for log_medication_taken/);
  });

  test("a mock that only matches some calls is refused", () => {
    // Collapsed to a map it becomes unconditional, so a call the rule was
    // written to exclude gets the payload anyway.
    const problem = whyNotExportable(
      testCase({
        tool_mocks: [
          mock("log_mood", { type: "partial_match", args: { mood: "tired" } }),
        ],
      }),
    );
    expect(problem).toMatch(/only matches some calls/);
  });

  test("ordinary match-anything mocks are fine", () => {
    expect(
      whyNotExportable(
        testCase({ tool_mocks: [mock("log_mood", { type: "any" }), mock("log_outcome", { type: "any" })] }),
      ),
    ).toBeNull();
  });
});

describe("other fields the file format would drop", () => {
  test("a pinned model is refused", () => {
    // Same class as a judged criterion: it would sync back running on the
    // agent's own model, with nothing to say it had.
    expect(whyNotExportable(testCase({ llm_model: "gemini-3.1-flash-lite" }))).toMatch(
      /no field for a pinned model/,
    );
  });

  test("a blank criterion row is not mistaken for a criterion", () => {
    // The criteria editor leaves one behind, and the backend drops it — so it
    // must not block an export that loses nothing.
    expect(whyNotExportable(testCase({ metrics: ["  "] }))).toBeNull();
  });
});
