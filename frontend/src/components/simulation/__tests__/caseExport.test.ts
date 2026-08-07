import { describe, expect, test } from "bun:test";
import type { RawTestCase } from "@/lib/api";
import { agentSlug, caseFileName, toCaseFile, whyNotExportable } from "../caseExport";

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

  test("an unnamed agent still produces something the sync can reject loudly", () => {
    expect(agentSlug("  ")).toBe("clara-checkin");
  });
});

describe("the emitted file", () => {
  test("is valid JavaScript that exports the case", () => {
    // Parsed rather than string-matched: the point of this module is that what
    // it writes can be `require`d by the sync, and only evaluating it says so.
    const source = toCaseFile(testCase(), { agentName: "clara-checkin", today: "2026-08-07" });
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
    const source = toCaseFile(testCase(), { agentName: "clara-checkin", today: "2026-08-07" });
    const exported = evaluated(source);
    const [first] = exported.assert as Record<string, unknown>[];
    expect(Object.keys(first)[0]).toBe("tool_called");
  });

  test("a clock pinned to the export date folds back to {{today}}", () => {
    // Left as a real date, the case grades that one calendar day forever and
    // the morning/evening branches stop moving with it.
    const source = toCaseFile(testCase(), { agentName: "clara-checkin", today: "2026-08-07" });
    const exported = evaluated(source);
    expect((exported.variables as Record<string, string>).current_time).toBe(
      "{{today}}T08:15",
    );
  });

  test("a date that is not today means that date and is left alone", () => {
    // A trial start or a last-assessment stamp is not "today" — rewriting it
    // would change what the case tests.
    const source = toCaseFile(
      testCase({ dynamic_variables: { trial_started: "2026-01-15" } }),
      { agentName: "clara-checkin", today: "2026-08-07" },
    );
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
      { agentName: "clara-checkin", today: "2026-08-07" },
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
      { agentName: "clara-checkin", today: "2026-08-07" },
    );
    const exported = evaluated(source);
    expect(exported.tools).toEqual({ kb_lookup: "not json at all" });
  });

  test("the same case exported twice produces the same file", () => {
    // `today` is an argument and not the clock, so a re-export diffs only where
    // the case actually changed.
    const once = toCaseFile(testCase(), { agentName: "clara-checkin", today: "2026-08-07" });
    const twice = toCaseFile(testCase(), { agentName: "clara-checkin", today: "2026-08-07" });
    expect(once).toBe(twice);
  });

  test("a name with a quote in it does not break the file", () => {
    const source = toCaseFile(testCase({ name: 'C01 the caller says "not today"' }), {
      agentName: "clara-checkin",
      today: "2026-08-07",
    });
    const exported = evaluated(source);
    expect(exported.name).toBe('C01 the caller says "not today"');
  });
});
