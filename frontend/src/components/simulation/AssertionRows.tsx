"use client";

import { TextInput } from "@/components/ui/Field";
import { Plus, Trash2 } from "lucide-react";
import {
  ASSERTION_HINTS,
  ASSERTION_KINDS,
  ASSERTION_LABELS,
  CALLER_HANGUP,
  argumentPatterns,
  assertionKind,
  assertionProblem,
  blankAssertion,
  changeKind,
  withExtra,
  withValue,
  type ArgumentPattern,
  type Assertion,
  type AssertionKind,
} from "./assertionModel";

const TOOL_LIST_ID = "assertion-tool-names";
const ENDING_LIST_ID = "assertion-ending-names";

/** Editor for a case's mechanical assertions.
 *
 * Fully controlled: every row's state lives in the `assertions` array, which is
 * what makes index keys safe here even though rows are removable from the
 * middle.
 */
export default function AssertionRows({
  assertions,
  toolNames,
  onChange,
}: {
  assertions: Assertion[];
  /** Function names the agent actually has, offered in every function picker. */
  toolNames: string[];
  onChange: (assertions: Assertion[]) => void;
}) {
  const replace = (index: number, next: Assertion) =>
    onChange(assertions.map((a, i) => (i === index ? next : a)));

  return (
    <div className="space-y-2">
      {/* One pair of suggestion lists for every row, rather than one per input:
          repeating an element id is invalid, and the contents never differ. */}
      <datalist id={TOOL_LIST_ID}>
        {toolNames.map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>
      <datalist id={ENDING_LIST_ID}>
        {[CALLER_HANGUP, ...toolNames].map((name) => (
          <option key={name} value={name} />
        ))}
      </datalist>

      {assertions.map((assertion, index) => (
        <AssertionRow
          key={index}
          assertion={assertion}
          onChange={(next) => replace(index, next)}
          onRemove={() => onChange(assertions.filter((_, i) => i !== index))}
        />
      ))}
      <button
        onClick={() => onChange([...assertions, blankAssertion("tool_called")])}
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> Add assertion
      </button>
    </div>
  );
}

function AssertionRow({
  assertion,
  onChange,
  onRemove,
}: {
  assertion: Assertion;
  onChange: (assertion: Assertion) => void;
  onRemove: () => void;
}) {
  const kind = assertionKind(assertion);
  const problem = assertionProblem(assertion);

  return (
    <div className="rounded-lg border border-line p-2.5">
      <div className="flex items-center gap-2">
        {kind ? (
          <select
            value={kind}
            onChange={(e) => onChange(changeKind(assertion, e.target.value as AssertionKind))}
            aria-label="Assertion type"
            className="h-8 shrink-0 rounded-lg border border-line bg-white px-2 text-[13px] outline-none focus:border-accent cursor-pointer"
          >
            {ASSERTION_KINDS.map((k) => (
              <option key={k} value={k}>
                {ASSERTION_LABELS[k]}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-[13px] font-medium text-amber-700">
            {String(Object.keys(assertion)[0] ?? "empty")}
          </span>
        )}
        <div className="min-w-0 grow">
          {kind && <ValueField assertion={assertion} kind={kind} onChange={onChange} />}
        </div>
        <button
          onClick={onRemove}
          aria-label="Remove assertion"
          className="shrink-0 rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>

      {kind === "tool_called" && (
        <ToolCalledExtras assertion={assertion} onChange={onChange} />
      )}

      {kind ? (
        <p className="mt-1.5 text-xs text-sub">{ASSERTION_HINTS[kind]}</p>
      ) : (
        <p className="mt-1.5 text-xs text-amber-700">
          This assertion type is not editable here — it was written through the API. It is saved
          back exactly as it arrived.
        </p>
      )}

      {problem && <p className="mt-1.5 text-xs text-bad">{problem}</p>}
    </div>
  );
}

/** The type's own value: a function name, a pattern, an order, or a count. */
function ValueField({
  assertion,
  kind,
  onChange,
}: {
  assertion: Assertion;
  kind: AssertionKind;
  onChange: (assertion: Assertion) => void;
}) {
  const value = assertion[kind];

  if (kind === "tool_order") {
    const steps = Array.isArray(value) ? value.map((v) => String(v ?? "")) : [];
    return (
      <div className="space-y-1.5">
        {steps.map((step, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <span className="w-4 shrink-0 text-right text-xs text-faint">{i + 1}</span>
            <ToolInput
              value={step}
              listId={TOOL_LIST_ID}
              onChange={(next) =>
                onChange(withValue(assertion, steps.map((s, j) => (j === i ? next : s))))
              }
            />
            <button
              onClick={() => onChange(withValue(assertion, steps.filter((_, j) => j !== i)))}
              aria-label="Remove step"
              className="shrink-0 rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
            >
              <Trash2 className="size-3.5" />
            </button>
          </div>
        ))}
        <button
          onClick={() => onChange(withValue(assertion, [...steps, ""]))}
          className="inline-flex items-center gap-1 text-xs font-medium text-accent-deep hover:underline cursor-pointer"
        >
          <Plus className="size-3" /> Add function
        </button>
      </div>
    );
  }

  if (kind === "turn_count_max") {
    return (
      <TextInput
        inputMode="numeric"
        value={String(value ?? "")}
        onChange={(e) => onChange(withValue(assertion, e.target.value))}
        placeholder="8"
        aria-label="Maximum caller turns"
      />
    );
  }

  if (kind === "said_matches" || kind === "said_never") {
    return (
      <TextInput
        className="font-mono text-[12px]"
        value={String(value ?? "")}
        onChange={(e) => onChange(withValue(assertion, e.target.value))}
        placeholder="(?i)transfer you"
        aria-label="Pattern"
      />
    );
  }

  return (
    <ToolInput
      value={String(value ?? "")}
      listId={kind === "ends_with" ? ENDING_LIST_ID : TOOL_LIST_ID}
      onChange={(next) => onChange(withValue(assertion, next))}
    />
  );
}

/** A function name, offered from what the agent has but not restricted to it.
 *
 * Free text on purpose. A select would snap a case that names a function the
 * agent no longer carries onto whichever function happens to sort first, which
 * silently changes what the case asserts — the one edit nobody would review.
 */
function ToolInput({
  value,
  listId,
  onChange,
}: {
  value: string;
  listId: string;
  onChange: (value: string) => void;
}) {
  return (
    <TextInput
      list={listId}
      className="font-mono text-[12px]"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder="log_mood"
      aria-label="Function name"
    />
  );
}

/** `tool_called`'s two optional narrowings: argument patterns, and a count. */
function ToolCalledExtras({
  assertion,
  onChange,
}: {
  assertion: Assertion;
  onChange: (assertion: Assertion) => void;
}) {
  const rows = argumentPatterns(assertion);
  const times = String(assertion.times ?? "");

  const setRows = (next: ArgumentPattern[]) =>
    onChange(withExtra(assertion, "with", next.length ? next : undefined));

  return (
    <div className="mt-2 space-y-1.5 border-t border-line/70 pt-2 pl-1">
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <TextInput
            className="font-mono text-[12px]"
            value={row.key}
            onChange={(e) => setRows(rows.map((r, j) => (j === i ? { ...r, key: e.target.value } : r)))}
            placeholder="medication_name"
            aria-label="Argument name"
          />
          <span className="shrink-0 text-xs text-sub">matches</span>
          <TextInput
            className="font-mono text-[12px]"
            value={row.value}
            onChange={(e) =>
              setRows(rows.map((r, j) => (j === i ? { ...r, value: e.target.value } : r)))
            }
            placeholder="(?i)lipitor"
            aria-label="Argument pattern"
          />
          <button
            onClick={() => setRows(rows.filter((_, j) => j !== i))}
            aria-label="Remove argument pattern"
            className="shrink-0 rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button
          onClick={() => setRows([...rows, { key: "", value: "" }])}
          className="inline-flex items-center gap-1 text-xs font-medium text-accent-deep hover:underline cursor-pointer"
        >
          <Plus className="size-3" /> Add argument pattern
        </button>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-sub">
          exactly
          <input
            inputMode="numeric"
            value={times}
            onChange={(e) =>
              onChange(withExtra(assertion, "times", e.target.value || undefined))
            }
            placeholder="any"
            aria-label="Exactly how many times"
            className="h-7 w-14 rounded-lg border border-line bg-white px-2 text-center text-[12px] outline-none focus:border-accent"
          />
          times
        </label>
      </div>
    </div>
  );
}
