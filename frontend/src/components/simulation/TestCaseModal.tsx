"use client";

import LlmModelSelect from "@/components/editor/LlmModelSelect";
import { PairRows, fromPairs, toPairs, type Pair } from "@/components/editor/PairRows";
import Button from "@/components/ui/Button";
import { Field, TextInput, Textarea } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import type { RawTestCase, TestCaseDraft, ToolMock } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import AssertionRows from "./AssertionRows";
import {
  assertionsProblem,
  formatScript,
  normalizeAssertions,
  parseScript,
  toEditableAssertions,
} from "./assertionModel";

const EMPTY: TestCaseDraft = {
  name: "",
  user_prompt: "",
  metrics: [""],
  tool_mocks: [],
  assertions: [],
};

/** Create/edit one simulation test case. `initial` null means "new case". */
export default function TestCaseModal({
  open,
  initial,
  toolNames,
  promptVariableNames,
  agentDefaultVariables,
  onClose,
  onSave,
}: {
  open: boolean;
  initial: RawTestCase | null;
  /** Function names the agent actually has — mocks may only target these. */
  toolNames: string[];
  /** Placeholders the agent's prompt reads, offered as one-click rows. */
  promptVariableNames: string[];
  /** The agent's fallback values — these need no per-case entry. */
  agentDefaultVariables: Record<string, string>;
  onClose: () => void;
  onSave: (draft: TestCaseDraft) => Promise<void>;
}) {
  const [draft, setDraft] = useState<TestCaseDraft>(EMPTY);
  const [variables, setVariables] = useState<Pair[]>([]);
  // Held as text, not as the parsed turns: re-splitting under the cursor would
  // swallow the blank line you are in the middle of typing a turn onto.
  const [scriptText, setScriptText] = useState("");
  // Which caller the case uses. A case is scripted to the engine when its
  // `script` is non-empty; this is that fact while it is still being typed,
  // so switching to Improvised does not have to destroy the lines to take
  // effect, and switching back finds them still there.
  const [scripted, setScripted] = useState(false);
  // A caller turn the textarea cannot represent, noticed at load because that
  // is the only moment the turn boundaries are still known. One turn per line
  // is what makes pasting a transcript work; the cost is that a turn already
  // containing a line break would be silently split in two, changing the
  // conversation and shifting it against any `turn_count_max`.
  const [splitTurn, setSplitTurn] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reload the form whenever the modal opens on a different case.
  useEffect(() => {
    if (!open) return;
    setError(null);
    setVariables(toPairs(initial?.dynamic_variables));
    setScriptText(formatScript(initial?.script));
    setScripted((initial?.script?.length ?? 0) > 0);
    setSplitTurn((initial?.script ?? []).some((line) => line.includes("\n")));
    setDraft(
      initial
        ? {
            name: initial.name,
            user_prompt: initial.user_prompt,
            // Verbatim, including none at all: a case graded entirely by its
            // assertions would otherwise open with a blank criterion row, which
            // reads as one somebody forgot to fill in.
            metrics: initial.metrics,
            tool_mocks: initial.tool_mocks ?? [],
            assertions: toEditableAssertions(initial.assertions),
            llm_model: initial.llm_model ?? null,
          }
        : EMPTY,
    );
  }, [open, initial]);

  // Names the prompt reads that this case leaves to the agent defaults — or,
  // when there is no default, to nothing at all. Offered as one-click rows so
  // a gated branch isn't unreachable just because a flag was never discovered.
  const setNames = new Set(variables.map((p) => p.key.trim()));
  const unset = promptVariableNames.filter((n) => !setNames.has(n));

  const setMetric = (i: number, value: string) =>
    setDraft((d) => ({ ...d, metrics: d.metrics.map((m, j) => (j === i ? value : m)) }));

  const setMock = (i: number, patch: Partial<ToolMock>) =>
    setDraft((d) => ({
      ...d,
      tool_mocks: (d.tool_mocks ?? []).map((m, j) => (j === i ? { ...m, ...patch } : m)),
    }));

  const save = async () => {
    const metrics = draft.metrics.map((m) => m.trim()).filter(Boolean);
    // Empty unless this case is scripted: an improvising case that kept a
    // leftover script would still be played from it, because the engine decides
    // by `script` alone.
    const script = scripted ? parseScript(scriptText) : [];
    const assertions = normalizeAssertions(draft.assertions ?? []);

    if (scripted && script.length === 0) {
      setError("Write the caller's turns, one per line — a scripted case needs at least one.");
      return;
    }
    if (!scripted && !draft.user_prompt.trim()) {
      setError("Describe what the simulated caller should do.");
      return;
    }
    const badAssertion = assertionsProblem(draft.assertions ?? []);
    if (badAssertion) {
      setError(badAssertion);
      return;
    }
    // The same rule the engine grades by: criteria and assertions together, so
    // either alone is enough and neither is not. A case with neither runs the
    // whole conversation and then reports nothing about it.
    if (metrics.length === 0 && assertions.length === 0) {
      setError("Add a success criterion or an assertion — a case with neither is never graded.");
      return;
    }

    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...draft,
        name: draft.name.trim() || "Untitled test",
        metrics,
        script,
        assertions,
        // `?? {}` and not `undefined`: the form owns these now, so clearing
        // every row has to reach the server as "no variables" rather than
        // being omitted and leaving the old set in place.
        dynamic_variables: fromPairs(variables) ?? {},
        tool_mocks: (draft.tool_mocks ?? []).filter((m) => m.tool_name),
      });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save the test case");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={initial ? "Edit test case" : "New test case"}
      width="max-w-2xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : initial ? "Save changes" : "Create test case"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <TextInput
            autoFocus
            value={draft.name}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
            placeholder="Caller reschedules an appointment"
          />
        </Field>

        <Field
          label="Caller"
          hint={
            scripted
              ? "The caller says exactly these lines, in order, and hangs up at the end. Same words every run, so a red result means the prompt changed and nothing else."
              : "The caller improvises from this description. Good at finding paths nobody wrote down, and a different conversation each run — so it cannot tell you whether an edit was safe."
          }
          right={
            <div className="inline-flex rounded-lg border border-line p-0.5">
              {[
                { mode: false, label: "Improvised" },
                { mode: true, label: "Scripted" },
              ].map(({ mode, label }) => (
                <button
                  key={label}
                  onClick={() => setScripted(mode)}
                  className={cn(
                    "cursor-pointer rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                    scripted === mode ? "bg-app text-ink" : "text-sub hover:text-ink",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          }
        >
          {scripted ? (
            <>
              <Textarea
                rows={6}
                className="font-mono text-[12px]"
                value={scriptText}
                onChange={(e) => setScriptText(e.target.value)}
                placeholder={"Morning Clara.\nI took the Lipitor.\nNo, that's everything."}
              />
              <p className="mt-1.5 text-xs text-sub">
                One caller turn per line — {parseScript(scriptText).length}
                {" so far. Paste the caller's side of a real transcript to pin a bug you have"}
                {" just seen."}
              </p>
              {splitTurn && (
                <p className="mt-1.5 text-xs text-amber-700">
                  One of this case&apos;s caller turns contains a line break, which this editor
                  cannot show — it reads one turn per line. Saving will split that turn in two,
                  changing the conversation. Edit the case file instead, or re-word the turn onto
                  one line.
                </p>
              )}
            </>
          ) : (
            <Textarea
              rows={5}
              value={draft.user_prompt}
              onChange={(e) => setDraft({ ...draft, user_prompt: e.target.value })}
              placeholder="You are Dana, calling to move your Tuesday appointment to Friday. You don't remember the exact time and get impatient if the agent repeats itself."
            />
          )}
        </Field>

        <Field
          label="Success criteria"
          hint="Graded by a model reading the finished transcript. Good for judgement calls no pattern can express, and a noisy sensor — two identical re-runs of the same suite scored 69 and 62. Prefer an assertion wherever one fits."
          right={
            <button
              onClick={() => setDraft({ ...draft, metrics: [...draft.metrics, ""] })}
              className="flex items-center gap-1 text-xs font-medium text-accent-deep hover:underline cursor-pointer"
            >
              <Plus className="size-3.5" /> Add criterion
            </button>
          }
        >
          <div className="space-y-2">
            {draft.metrics.map((metric, i) => (
              <div key={i} className="flex items-center gap-2">
                <TextInput
                  value={metric}
                  onChange={(e) => setMetric(i, e.target.value)}
                  placeholder="The agent confirms the new date before ending the call"
                />
                {/* Removable down to none: a case graded entirely by
                    assertions wants no judged criteria at all, and leaving a
                    blank row behind reads as one that was forgotten. */}
                <button
                  onClick={() =>
                    setDraft({ ...draft, metrics: draft.metrics.filter((_, j) => j !== i) })
                  }
                  aria-label="Remove criterion"
                  className="shrink-0 rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
            ))}
            {draft.metrics.length === 0 && (
              <p className="text-xs text-sub">
                None — this case is graded by its assertions alone, with no model involved.
              </p>
            )}
          </div>
        </Field>

        <Field
          label="Assertions"
          hint="Graded by code, not by a model, so every one of them decides the same way every run. This is the half of a case that can be a gate."
        >
          <AssertionRows
            assertions={draft.assertions ?? []}
            toolNames={toolNames}
            onChange={(assertions) => setDraft({ ...draft, assertions })}
          />
          {!scripted && (draft.assertions?.length ?? 0) > 0 && (
            <p className="mt-2 text-xs text-amber-700">
              These grade the same way every run, but an improvising caller walks a different path
              each time — so they are being applied to a different conversation each time. Switch
              the caller to Scripted to get a repeatable result.
            </p>
          )}
        </Field>

        <Field
          label="Dynamic variables"
          hint="The state the agent runs in for this scenario. An unset variable stays literal, so a branch the prompt gates on one never fires. Set current_time to a timestamp (2026-07-27T08:15) to pin the clock the call runs on — a step the prompt gates on the time is otherwise out of reach except at that hour."
        >
          <PairRows
            addLabel="Add variable"
            pairs={variables}
            onChange={setVariables}
            keyPlaceholder="is_last_day_of_trial"
            valuePlaceholder="true"
          />
          {unset.length > 0 && (
            <p className="mt-2 text-xs text-sub">
              Read by the prompt, not set here:{" "}
              {unset.map((name, i) => (
                <span key={name}>
                  {i > 0 && ", "}
                  <button
                    // Seeded with the agent default, never blank: a case value
                    // beats the default at run time, so adding an empty row
                    // would replace "friend" with "" — worse than not clicking.
                    onClick={() =>
                      setVariables([
                        ...variables,
                        { key: name, value: agentDefaultVariables[name] ?? "" },
                      ])
                    }
                    title={
                      name in agentDefaultVariables
                        ? `Add it, starting from the agent default: ${agentDefaultVariables[name]}`
                        : "No value anywhere — stays literal at run time"
                    }
                    className={`font-mono cursor-pointer hover:underline ${
                      name in agentDefaultVariables ? "text-sub" : "text-accent-deep"
                    }`}
                  >
                    {name}
                  </button>
                </span>
              ))}
            </p>
          )}
        </Field>

        <Field
          label="Function mocks"
          hint={
            toolNames.length
              ? "Pin what a function returns so the run is deterministic. Unmocked functions get a plausible made-up result; nothing is ever really called."
              : "This agent has no functions to mock."
          }
          right={
            toolNames.length ? (
              <button
                onClick={() =>
                  setDraft({
                    ...draft,
                    tool_mocks: [
                      ...(draft.tool_mocks ?? []),
                      {
                        tool_name: toolNames[0],
                        input_match_rule: { type: "any" },
                        output: "{}",
                      },
                    ],
                  })
                }
                className="flex items-center gap-1 text-xs font-medium text-accent-deep hover:underline cursor-pointer"
              >
                <Plus className="size-3.5" /> Add mock
              </button>
            ) : undefined
          }
        >
          <div className="space-y-2">
            {(draft.tool_mocks ?? []).map((mock, i) => (
              <div key={i} className="rounded-lg border border-line p-2.5">
                <div className="flex items-center gap-2">
                  <select
                    value={mock.tool_name}
                    onChange={(e) => setMock(i, { tool_name: e.target.value })}
                    className="h-8 rounded-lg border border-line bg-white px-2 text-[13px] outline-none focus:border-accent cursor-pointer"
                  >
                    {toolNames.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                  <span className="text-xs text-sub">returns</span>
                  <button
                    onClick={() =>
                      setDraft({
                        ...draft,
                        tool_mocks: (draft.tool_mocks ?? []).filter((_, j) => j !== i),
                      })
                    }
                    aria-label="Remove mock"
                    className="ml-auto rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
                <Textarea
                  rows={2}
                  className="mt-2 font-mono text-[12px]"
                  value={mock.output}
                  onChange={(e) => setMock(i, { output: e.target.value })}
                  placeholder='{"available": false}'
                />
              </div>
            ))}
          </div>
        </Field>

        <Field
          label="Model"
          hint="Which model plays the agent in this simulation. Leave it on the agent's own unless you are comparing models — a realtime-audio model can't generate text, so those are stood in for automatically."
        >
          <div className="flex items-center gap-2">
            <LlmModelSelect
              value={draft.llm_model ?? ""}
              onChange={(v) => setDraft({ ...draft, llm_model: v || null })}
            />
            {draft.llm_model && (
              <button
                onClick={() => setDraft({ ...draft, llm_model: null })}
                className="text-xs font-medium text-accent-deep hover:underline cursor-pointer"
              >
                Use the agent&apos;s own
              </button>
            )}
          </div>
        </Field>

        {error && <p className="text-[13px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
