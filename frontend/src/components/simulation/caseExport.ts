/** Turning a saved test case back into a `.case.js` file.
 *
 * The dashboard is the fastest place to *write* a scripted case — you have just
 * heard a bug on a real call and the transcript is in front of you. It is not
 * where a case *lives*: an assertion that can be weakened in a browser with
 * nobody looking is not a gate, because the suite stays green and everyone
 * keeps trusting it. So a draft written here gets exported to a file, reviewed
 * in a PR, and synced back from then on.
 *
 * This is the exact inverse of `prompts/clara/tests/runner/translate.js` in
 * `usan-retirement-backend`, which turns a case file into the request body this
 * reads back. Everything here is pure so the round trip is testable.
 *
 * **Nothing from the case is interpolated into the file except through
 * `JSON.stringify`.** The emitted file is `require`d by the sync and the
 * runner, so a value that escapes its literal is arbitrary code running on
 * whoever runs the suite — and a case name is attacker-controlled by anyone who
 * can write to the workspace.
 */

import type { Assertion, RawTestCase, ToolMock } from "@/lib/api";

/** An ISO date, as `translate.js` leaves one when it expands `{{today}}`. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;
/** `YYYY-MM-DD` — what `{{today}}` expanded to, and what folds back into it. */
const ISO_DATE_LENGTH = 10;

/** The variable that pins the clock. `validate.js` singles it out by name too. */
const CLOCK = "current_time";

/** Used when the agent has no display name to slug. Deliberately not a real
 *  key: it has to stop the sync, not resolve to somebody else's prompt. */
export const UNKNOWN_AGENT = "UNKNOWN-AGENT-fix-me";

/**
 * Why this case cannot become a case file, or null when it can.
 *
 * Refusing beats exporting something lossy. The file format carries `name`,
 * `agent`, `variables`, `tools`, `script` and `assert` and nothing else —
 * `translate.js` reads no other key — so anything else written into a file is
 * dropped in silence at the next sync, and the case comes back grading less
 * than it did.
 */
export function whyNotExportable(testCase: RawTestCase): string | null {
  if ((testCase.script?.length ?? 0) === 0) {
    return "Only a scripted case can be exported — an improvised scenario has no case-file form.";
  }
  if ((testCase.assertions?.length ?? 0) === 0) {
    return "Add at least one assertion. A case file carries no judged criteria, so this would export as a conversation that grades nothing.";
  }
  if (testCase.metrics.some((metric) => metric.trim())) {
    return "A case file has no field for a judged criterion, so exporting would drop it. Remove the criteria, or keep this case in the dashboard.";
  }
  if (testCase.llm_model) {
    return `A case file has no field for a pinned model, so this would export and sync back running on the agent's own model instead of ${testCase.llm_model}.`;
  }
  return mockProblem(testCase.tool_mocks ?? []);
}

/**
 * Why a case's mocks cannot be written as a `{name: output}` map.
 *
 * The file format has one payload per tool and no place for an
 * `input_match_rule`, so two shapes cannot survive the trip:
 *
 * * **Two mocks for one tool.** A map keeps one of them — and it would keep the
 *   *last*, while `match_tool_mock` uses the *first*. So the exported case
 *   would not merely lose a mock, it would keep the opposite one and grade a
 *   different conversation.
 * * **A `partial_match` rule.** Collapsed to a map it becomes unconditional, so
 *   a call the rule was written to exclude gets the payload anyway.
 */
function mockProblem(mocks: ToolMock[]): string | null {
  const seen = new Set<string>();
  for (const mock of mocks) {
    if (seen.has(mock.tool_name)) {
      return `Two mocks for ${mock.tool_name}. A case file has one payload per function, and exporting would keep the wrong one — the engine uses the first match, a map keeps the last.`;
    }
    seen.add(mock.tool_name);
    const rule = mock.input_match_rule?.type ?? "any";
    if (rule !== "any") {
      return `The mock for ${mock.tool_name} only matches some calls (${rule}). A case file cannot express that, so exporting would mock every call to it.`;
    }
  }
  return null;
}

/** A case name as a filename: `M01 morning dose …` → `M01-morning-dose.case.js`. */
export function caseFileName(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .split("-")
    .slice(0, 6)
    .join("-");
  return `${slug || "case"}.case.js`;
}

/** An agent's display name as the key `dist/agent-ids.json` would use. */
export function agentSlug(agentName: string): string {
  return (
    agentName
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || UNKNOWN_AGENT
  );
}

/**
 * The pinned clock back into `{{today}}`.
 *
 * `translate.js` expands `{{today}}` on the way up, so a case that came from a
 * file carries a real date by the time it is read back — yesterday's, if it was
 * synced yesterday. Left as one, the case grades that calendar day forever and
 * every morning/evening branch stops moving with it.
 *
 * Keyed on the variable *name* rather than on the date matching today, which is
 * what `validate.js` does too: `current_time` is the clock pin and a fixed date
 * there is the bug that rule exists to catch, whichever day it names. Every
 * other variable is left exactly as it is — a trial start or an assessment
 * stamp *means* that date, and rewriting it would change what the case tests.
 *
 * The cost is a case deliberately pinned to a historical date, which comes back
 * as `{{today}}`. That is not a thing the case set does, and the alternative —
 * folding only today's date — silently hard-codes the clock on every export
 * after the day the case was written.
 */
function foldClock(name: string, value: string): string {
  if (name !== CLOCK || !ISO_DATE.test(value)) return value;
  return `{{today}}${value.slice(ISO_DATE_LENGTH)}`;
}

/** `tool_mocks` back into the case file's `{name: output}` map.
 *
 * Only reached once `mockProblem` has ruled out the shapes a map cannot hold.
 *
 * A payload written in a fixture as the string `'{"ok": true}'` comes back as
 * the object `{ok: true}`, so re-exporting an existing case rewrites its mock
 * whitespace. The two are the same JSON and the engine reads them identically —
 * worth knowing only so the diff on a re-export is not mistaken for a change in
 * what the case does.
 */
function toolsBlock(testCase: RawTestCase): Record<string, unknown> {
  const tools: Record<string, unknown> = {};
  for (const mock of testCase.tool_mocks ?? []) {
    try {
      const parsed = JSON.parse(mock.output);
      tools[mock.tool_name] = typeof parsed === "object" && parsed !== null ? parsed : mock.output;
    } catch {
      tools[mock.tool_name] = mock.output;
    }
  }
  return tools;
}

function variablesBlock(testCase: RawTestCase): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(testCase.dynamic_variables ?? {})) {
    out[name] = foldClock(name, String(value));
  }
  return out;
}

/** A value as a JS literal, indented to sit inside `module.exports`. */
function literal(value: unknown, indent: number): string {
  return JSON.stringify(value, null, 2).split("\n").join("\n" + " ".repeat(indent));
}

/**
 * The `.case.js` source for a case, ready to be committed.
 *
 * A function of its input alone — no clock is read — so the same case exported
 * twice produces the same file and a re-export diffs only where the case
 * actually changed.
 *
 * The header comment carries no value from the case. A name containing a block
 * comment terminator would otherwise close the comment early and run whatever
 * followed it, in a file whose entire purpose is to be `require`d by the sync.
 */
export function toCaseFile(testCase: RawTestCase, { agentName }: { agentName: string }): string {
  const assertions = (testCase.assertions ?? []) as Assertion[];
  return `"use strict";

/** Exported from the Arhiteq dashboard. Before committing:
 *
 *  - \`agent\` must match a key in \`dist/agent-ids.json\`. It is guessed here
 *    from the agent this was exported from, and a wrong one stops the sync with
 *    this file's path rather than grading the wrong prompt.
 *  - Mock every tool the conversation can reach, not only the ones asserted on.
 *    An unmocked tool has its result invented by a model, and that invented
 *    payload steers every turn after it.
 *  - \`_fixtures.js\` supplies the whole variable set for an agent. Prefer it to
 *    the literal map below, which is only what this case happened to pin.
 */
module.exports = {
  name: ${JSON.stringify(testCase.name)},
  agent: ${JSON.stringify(agentSlug(agentName))},
  variables: ${literal(variablesBlock(testCase), 2)},
  tools: ${literal(toolsBlock(testCase), 2)},
  script: ${literal(testCase.script ?? [], 2)},
  assert: ${literal(assertions, 2)},
};
`;
}
