"use client";

import { PairRows, fromPairs, toPairs, type Pair } from "@/components/editor/PairRows";
import Button from "@/components/ui/Button";
import { Field, TextInput, Textarea } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import type { RawTestCase, TestCaseDraft, ToolMock } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

const EMPTY: TestCaseDraft = { name: "", user_prompt: "", metrics: [""], tool_mocks: [] };

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
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reload the form whenever the modal opens on a different case.
  useEffect(() => {
    if (!open) return;
    setError(null);
    setVariables(toPairs(initial?.dynamic_variables));
    setDraft(
      initial
        ? {
            name: initial.name,
            user_prompt: initial.user_prompt,
            metrics: initial.metrics.length ? initial.metrics : [""],
            tool_mocks: initial.tool_mocks ?? [],
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
    if (!draft.user_prompt.trim()) {
      setError("Describe what the simulated caller should do.");
      return;
    }
    if (metrics.length === 0) {
      setError("Add at least one success criterion to grade the run against.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({
        ...draft,
        name: draft.name.trim() || "Untitled test",
        metrics,
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
          label="Scenario"
          hint="Instructions for the simulated caller: who they are, what they want, and how they behave."
        >
          <Textarea
            rows={5}
            value={draft.user_prompt}
            onChange={(e) => setDraft({ ...draft, user_prompt: e.target.value })}
            placeholder="You are Dana, calling to move your Tuesday appointment to Friday. You don't remember the exact time and get impatient if the agent repeats itself."
          />
        </Field>

        <Field
          label="Success criteria"
          hint="Each is graded separately after the call. The run passes only if all of them pass."
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
                {draft.metrics.length > 1 && (
                  <button
                    onClick={() =>
                      setDraft({ ...draft, metrics: draft.metrics.filter((_, j) => j !== i) })
                    }
                    aria-label="Remove criterion"
                    className="shrink-0 rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </Field>

        <Field
          label="Dynamic variables"
          hint="The state the agent runs in for this scenario. An unset variable stays literal, so a branch the prompt gates on one never fires."
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

        {error && <p className="text-[13px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
