/** The seven mechanical assertions, as the API stores them.
 *
 * Pure, so the rules that decide whether a case can be saved are testable
 * without rendering anything. `AssertionRows` is the editor over this.
 *
 * **Key order is load-bearing.** The backend reads an assertion's type from its
 * *first* key (`services/assertions.py`, `next(iter(assertion))`), which is why
 * the column is JSON and not JSONB — JSONB reparses to an object and does not
 * preserve order. Every constructor here writes the type key first, and nothing
 * may rebuild an assertion by spreading another object over a type key.
 */

import type { Assertion } from "@/lib/api";

export type { Assertion };

/** One `tool_called` argument pattern, while it is being edited.
 *
 * The wire shape of `with` is a map of argument name to pattern. The editing
 * shape is a list, because a map cannot hold two rows whose names are both
 * still blank — adding a second row would replace the first, and typing a
 * pattern before its argument name would lose it. `toEditableAssertions` and
 * `normalizeAssertions` are the two ends of that conversion.
 */
export type ArgumentPattern = { key: string; value: string };

export const ASSERTION_KINDS = [
  "tool_called",
  "tool_not_called",
  "tool_order",
  "said_matches",
  "said_never",
  "ends_with",
  "turn_count_max",
] as const;

export type AssertionKind = (typeof ASSERTION_KINDS)[number];

/** What the picker shows for each type. */
export const ASSERTION_LABELS: Record<AssertionKind, string> = {
  tool_called: "Called a function",
  tool_not_called: "Never called a function",
  tool_order: "Called functions in order",
  said_matches: "Said something matching",
  said_never: "Never said anything matching",
  ends_with: "Ended the call with",
  turn_count_max: "Took at most N caller turns",
};

/** The one-line explanation under a row, written for whoever writes the case. */
export const ASSERTION_HINTS: Record<AssertionKind, string> = {
  tool_called:
    "Passes when the function ran. Add argument patterns to require that one single call matched all of them.",
  tool_not_called: "Passes when the function never ran at all.",
  tool_order:
    "Passes when these ran in this relative order. Other calls in between are fine.",
  said_matches: "Passes when some agent turn matches. Tool arguments are not searched.",
  said_never: "Passes when no agent turn matches.",
  ends_with:
    "The last function called, or caller_hangup for a call the caller ended themselves.",
  turn_count_max: "Fails when the caller needed more turns than this.",
};

/** The types whose value names a function the agent has. */
const TOOL_NAMED: ReadonlySet<string> = new Set(["tool_called", "tool_not_called", "ends_with"]);

/** The types whose value is a regex, plus every value inside `with`. */
const PATTERN_VALUED: ReadonlySet<string> = new Set(["said_matches", "said_never"]);

/** `ends_with` accepts this instead of a function name. */
export const CALLER_HANGUP = "caller_hangup";

function isKind(key: string): key is AssertionKind {
  return (ASSERTION_KINDS as readonly string[]).includes(key);
}

/** The assertion's type, or null when it carries one this editor does not model.
 *
 * Null is not a defect: the API accepts any object, so a case written by script
 * can hold a type added later. The editor shows those read-only rather than
 * rewriting them into something it does understand.
 */
export function assertionKind(assertion: Assertion): AssertionKind | null {
  const first = Object.keys(assertion)[0];
  return first && isKind(first) ? first : null;
}

/** A new, empty assertion of this type. */
export function blankAssertion(kind: AssertionKind): Assertion {
  if (kind === "tool_order") return { tool_order: [] };
  if (kind === "turn_count_max") return { turn_count_max: "" };
  return { [kind]: "" };
}

/** Retype an assertion, carrying the value across when both types take one.
 *
 * Two function-named types keep the function; two pattern types keep the
 * pattern. Anything else starts empty, because carrying a regex into a slot
 * that wants a function name produces an assertion that reads as deliberate and
 * can never pass.
 */
export function changeKind(assertion: Assertion, kind: AssertionKind): Assertion {
  const from = assertionKind(assertion);
  if (from === kind) return assertion;
  const value = from ? assertion[from] : undefined;
  const carries =
    from !== null &&
    ((TOOL_NAMED.has(from) && TOOL_NAMED.has(kind)) ||
      (PATTERN_VALUED.has(from) && PATTERN_VALUED.has(kind)));
  return carries ? { [kind]: value } : blankAssertion(kind);
}

/** Rewrite one assertion's type value, leaving `with` and `times` in place.
 *
 * Rebuilt rather than mutated so the type key stays first even if a caller
 * hands in an object whose keys arrived in another order.
 */
export function withValue(assertion: Assertion, value: unknown): Assertion {
  const kind = assertionKind(assertion);
  if (!kind) return assertion;
  const rest = { ...assertion };
  delete rest[kind];
  return { [kind]: value, ...rest };
}

/** A `tool_called`'s argument patterns as rows, from either shape.
 *
 * Reads the editing list and the wire map alike, so the same validation runs
 * over a case straight from the API as over one being typed.
 */
export function argumentPatterns(assertion: Assertion): ArgumentPattern[] {
  const args = assertion.with;
  if (Array.isArray(args)) {
    return args.map((row) => ({
      key: String((row as ArgumentPattern)?.key ?? ""),
      value: String((row as ArgumentPattern)?.value ?? ""),
    }));
  }
  if (args && typeof args === "object") {
    return Object.entries(args as Record<string, unknown>).map(([key, value]) => ({
      key,
      value: String(value ?? ""),
    }));
  }
  return [];
}

/** Set (or clear, with undefined) one of `tool_called`'s two extras. */
export function withExtra(
  assertion: Assertion,
  key: "with" | "times",
  value: unknown,
): Assertion {
  const next = { ...assertion };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

/** A pattern wrapped in slashes, which Python matches literally.
 *
 * `re.search("/(?i)hello/", text)` looks for the slash characters, so the
 * assertion passes forever while asserting nothing — the single most expensive
 * way to write one of these wrong, because it looks right in review.
 */
function looksLikeSlashRegex(pattern: string): boolean {
  return /^\/.*\/[a-z]*$/.test(pattern.trim());
}

function patternProblem(pattern: string, where: string): string | null {
  if (!pattern.trim()) return `${where} needs a pattern.`;
  if (looksLikeSlashRegex(pattern)) {
    return `${where}: write the pattern on its own, not as /pattern/flags — the slashes are matched literally. Case-insensitivity goes inline as (?i).`;
  }
  return null;
}

/** Why this assertion cannot be saved, or null when it is fine. */
export function assertionProblem(assertion: Assertion): string | null {
  const kind = assertionKind(assertion);
  // An unmodelled type is left exactly as it arrived; the editor shows it
  // read-only and it is not this form's business to judge it.
  if (!kind) return null;
  const value = assertion[kind];

  if (TOOL_NAMED.has(kind)) {
    if (!String(value ?? "").trim()) return `${ASSERTION_LABELS[kind]} needs a function name.`;
  }

  if (PATTERN_VALUED.has(kind)) {
    const problem = patternProblem(String(value ?? ""), ASSERTION_LABELS[kind]);
    if (problem) return problem;
  }

  if (kind === "tool_order") {
    const steps = Array.isArray(value) ? value.filter((v) => String(v ?? "").trim()) : [];
    if (steps.length < 2) return "Called functions in order needs at least two functions.";
  }

  if (kind === "turn_count_max") {
    const turns = Number(String(value ?? "").trim());
    if (!Number.isInteger(turns) || turns < 1) {
      return "Took at most N caller turns needs a whole number of turns, at least 1.";
    }
  }

  if (kind === "tool_called") {
    const times = assertion.times;
    if (times !== undefined && String(times).trim() !== "") {
      const count = Number(String(times).trim());
      if (!Number.isInteger(count) || count < 0) {
        return "Exactly N times needs a whole number, zero or more.";
      }
      // The backend's "was never called" guard fires before `times` is read, so
      // this assertion can only ever fail. It is worth naming rather than
      // silently saving, because it reads as the obvious way to say "never".
      if (count === 0) {
        return 'Exactly 0 times can never pass — use "Never called a function" instead.';
      }
    }
    for (const { key, value: pattern } of argumentPatterns(assertion)) {
      // A row with neither half filled in is one that was added and abandoned;
      // `normalizeAssertions` drops it, so it is not worth blocking a save.
      if (!key.trim() && !pattern.trim()) continue;
      if (!key.trim()) return "An argument pattern is missing its argument name.";
      const problem = patternProblem(pattern, `Argument ${key.trim()}`);
      if (problem) return problem;
    }
  }

  return null;
}

/** The first problem across a list, prefixed with which row it is on. */
export function assertionsProblem(assertions: Assertion[]): string | null {
  for (const [index, assertion] of assertions.entries()) {
    const problem = assertionProblem(assertion);
    if (problem) return `Assertion ${index + 1}: ${problem}`;
  }
  return null;
}

/** The list as it should be stored: counts as numbers, blank rows dropped.
 *
 * Counts are typed as text and kept as text while editing, so that a half-typed
 * value is not repeatedly reinterpreted under the cursor. Saving is where they
 * become numbers.
 */
export function normalizeAssertions(assertions: Assertion[]): Assertion[] {
  const out: Assertion[] = [];
  for (const assertion of assertions) {
    const kind = assertionKind(assertion);
    if (!kind) {
      out.push(assertion);
      continue;
    }
    const value = assertion[kind];

    if (kind === "turn_count_max") {
      out.push({ turn_count_max: Number(String(value ?? "").trim()) });
      continue;
    }

    if (kind === "tool_order") {
      const steps = Array.isArray(value)
        ? value.map((v) => String(v ?? "").trim()).filter(Boolean)
        : [];
      out.push({ tool_order: steps });
      continue;
    }

    if (kind === "tool_called") {
      const next: Assertion = { tool_called: String(value ?? "").trim() };
      // Rows are added blank and filled in, so an unnamed one is an abandoned
      // row rather than an argument called "". A later row wins a repeated
      // name, which is the only reading a map allows.
      const kept = argumentPatterns(assertion).filter((row) => row.key.trim());
      if (kept.length) {
        next.with = Object.fromEntries(kept.map((row) => [row.key.trim(), row.value]));
      }
      const times = assertion.times;
      if (times !== undefined && String(times).trim() !== "") {
        next.times = Number(String(times).trim());
      }
      out.push(next);
      continue;
    }

    out.push({ [kind]: String(value ?? "").trim() });
  }
  return out;
}

/** A saved case's assertions, in the shape the editor works in.
 *
 * The inverse of `normalizeAssertions`: counts become text so a half-typed
 * value is not reinterpreted under the cursor, and `with` becomes a list of
 * rows so blank ones can exist while they are being filled in.
 */
export function toEditableAssertions(assertions: Assertion[] | undefined): Assertion[] {
  return (assertions ?? []).map((assertion) => {
    const kind = assertionKind(assertion);
    if (!kind) return assertion;
    const value = assertion[kind];

    if (kind === "tool_order") {
      return { tool_order: Array.isArray(value) ? value.map((v) => String(v ?? "")) : [] };
    }

    if (kind === "tool_called") {
      const next: Assertion = { tool_called: String(value ?? "") };
      const args = argumentPatterns(assertion);
      if (args.length) next.with = args;
      if (assertion.times !== undefined) next.times = String(assertion.times);
      return next;
    }

    return { [kind]: String(value ?? "") };
  });
}

/** Caller turns from the textarea: one per line, blanks dropped.
 *
 * Blank lines are how a paste from a transcript arrives, and an empty caller
 * turn would be spoken as silence and shift every later turn.
 */
export function parseScript(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** The stored turns back into editable text. */
export function formatScript(script: string[] | undefined): string {
  return (script ?? []).join("\n");
}
