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
 */

import type { Assertion, RawTestCase } from "@/lib/api";

/** An ISO date, as `translate.js` writes one when it expands `{{today}}`. */
const ISO_DATE = /^\d{4}-\d{2}-\d{2}/;

/**
 * Why this case cannot become a case file, or null when it can.
 *
 * Refusing beats exporting something lossy. The file format has no field for a
 * judged criterion, and `translate.js` reads only `name`, `agent`, `variables`,
 * `tools`, `script` and `assert` — so a criterion written into the file would
 * be dropped in silence at the next sync, and the case would come back graded
 * by less than it was.
 */
export function whyNotExportable(testCase: RawTestCase): string | null {
  if ((testCase.script?.length ?? 0) === 0) {
    return "Only a scripted case can be exported — an improvised scenario has no case-file form.";
  }
  if ((testCase.assertions?.length ?? 0) === 0) {
    return "Add at least one assertion. A case file carries no judged criteria, so this would export as a conversation that grades nothing.";
  }
  if (testCase.metrics.length > 0) {
    return "A case file has no field for a judged criterion, so exporting would drop it. Remove the criteria, or keep this case in the dashboard.";
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
      .replace(/^-+|-+$/g, "") || "clara-checkin"
  );
}

/**
 * A pinned clock back into `{{today}}`, when it was today that it was pinned.
 *
 * `translate.js` expands `{{today}}` on the way up, so a case authored here
 * carries a real date. Left as one, the case grades that calendar day forever
 * and the morning/evening branches stop moving with it.
 *
 * Only a date equal to `today` is folded back. A variable legitimately holding
 * some other date — a trial start, a last-assessment stamp — means that date,
 * and rewriting it would change what the case tests.
 */
function foldToday(value: string, today: string): string {
  return ISO_DATE.test(value) && value.startsWith(today)
    ? `{{today}}${value.slice(today.length)}`
    : value;
}

/** `tool_mocks` back into the case file's `{name: output}` map.
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
    // Emitted as an object when it is one, because that is how a case file is
    // written and read; a payload that is not JSON stays the string it was.
    try {
      const parsed = JSON.parse(mock.output);
      tools[mock.tool_name] = typeof parsed === "object" && parsed !== null ? parsed : mock.output;
    } catch {
      tools[mock.tool_name] = mock.output;
    }
  }
  return tools;
}

function variablesBlock(testCase: RawTestCase, today: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, value] of Object.entries(testCase.dynamic_variables ?? {})) {
    out[name] = foldToday(String(value), today);
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
 * `today` is passed in rather than read from the clock so the output is a
 * function of its input alone — the same case exported twice produces the same
 * file, and a re-export shows a diff only where the case actually changed.
 */
export function toCaseFile(
  testCase: RawTestCase,
  { agentName, today }: { agentName: string; today: string },
): string {
  const assertions = (testCase.assertions ?? []) as Assertion[];
  return `"use strict";

/** ${testCase.name}
 *
 *  Exported from the Arhiteq dashboard. Before committing:
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
  variables: ${literal(variablesBlock(testCase, today), 2)},
  tools: ${literal(toolsBlock(testCase), 2)},
  script: ${literal(testCase.script ?? [], 2)},
  assert: ${literal(assertions, 2)},
};
`;
}
