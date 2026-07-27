"use client";

import TestPanel from "@/components/editor/TestPanel";
import { SIDE_PANEL_WIDTH } from "@/components/editor/panelLayout";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import Modal from "@/components/ui/Modal";
import {
  api,
  type RawBatchTest,
  type RawLlm,
  type RawTestCase,
  type RawTestRun,
  type TestCaseDraft,
} from "@/lib/api";
import { promptVariables } from "@/lib/promptVariables";
import {
  FlaskConical,
  Loader2,
  Pencil,
  Play,
  Plus,
  Sparkles,
  Trash2,
  TriangleAlert,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import RunDrawer from "./RunDrawer";
import TestCaseModal from "./TestCaseModal";
import { RunStatusBadge } from "./runStatus";

/** How often a running batch is re-polled for verdicts. */
const POLL_MS = 2500;
/** How long to keep polling before treating a batch as stalled. Generous —
 *  a 1000-case batch is legitimately slow — but finite. */
const POLL_DEADLINE_MS = 30 * 60 * 1000;
/** Cases one "Generate tests" click drafts. The API caps this at 10; asking
 *  for the full ten in one call beats several smaller ones, because the model
 *  sees every slot at once and spreads the scenarios instead of re-deriving
 *  the same obvious ones (it is never shown the cases already saved). */
const GENERATE_COUNT = 10;
/** Past batches read on open, to show a last result for cases the newest run
 *  didn't include. */
const HISTORY_BATCHES = 5;

export default function SimulationTab({
  agentId,
  agentVersion,
  llm,
  dirty,
}: {
  agentId: string;
  /** Version open in the editor; the voice test dials this one, not the
   *  published default, so a draft can be tried before publishing. */
  agentVersion: number;
  llm: RawLlm | null;
  /** The editor has unsaved edits — runs would test the last saved prompt. */
  dirty: boolean;
}) {
  const llmId = llm?.llm_id ?? null;
  const [cases, setCases] = useState<RawTestCase[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [runs, setRuns] = useState<Record<string, RawTestRun>>({});
  const [batch, setBatch] = useState<RawBatchTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<null | "run" | "generate">(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<RawTestCase | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  // Keyed by test case, not by run: the drawer then re-renders from the poll
  // loop's fresh data instead of freezing on the run as it was when opened.
  const [openCaseId, setOpenCaseId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<RawTestCase | null>(null);

  const toolNames = (llm?.general_tools ?? [])
    .map((t) => (typeof t?.name === "string" ? t.name : (t?.type as string | undefined)))
    .filter((n): n is string => Boolean(n));

  // The greeting counts too: it is resolved with the same variables, and a
  // {{first_name}} left unset is why a run opens with "Good morning friend".
  const promptVariableNames = [
    ...new Set([
      ...promptVariables(llm?.general_prompt),
      ...promptVariables(llm?.begin_message),
    ]),
  ];
  const agentDefaultVariables = llm?.default_dynamic_variables ?? {};

  /** Fold runs into the per-case results, newest run per case winning.
   *  Merging (rather than replacing) is what lets the table show a verdict for
   *  every case, not just the ones in the batch that happened to run last. */
  const adoptRuns = useCallback((next: RawTestRun[]) => {
    setRuns((prev) => {
      const merged = { ...prev };
      for (const run of next) {
        const current = merged[run.test_case_definition_id];
        if (!current || current.creation_timestamp <= run.creation_timestamp) {
          merged[run.test_case_definition_id] = run;
        }
      }
      return merged;
    });
  }, []);

  useEffect(() => {
    if (!llmId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // The table needs the cases and nothing else, so the spinner waits on this
    // one request. Run history is a backfill for the "last result" column and
    // fills in behind it — gating the whole tab on it cost a round trip per
    // batch before anything at all was drawn.
    api
      .listTestCases(llmId)
      .then((caseList) => !cancelled && setCases(caseList.items))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load tests"))
      .finally(() => !cancelled && setLoading(false));

    // Backfill from the recent batches so a reopened editor still reports the
    // verdicts you got yesterday — a case only ever ran in one of them, and
    // the newest run per case wins when several did.
    api
      .listBatchTests(llmId, agentId)
      .catch(() => null)
      .then(async (batchList) => {
        if (cancelled || !batchList) return;
        const recent = batchList.items.slice(0, HISTORY_BATCHES);
        // Only claim the slot if nothing else has: the table is interactive
        // while this is still in flight, so "Run all" can have already put its
        // own in-progress batch here. Overwriting it with the newest *finished*
        // one from a list request issued before it existed would stop the poll
        // loop and drop the run the operator is watching.
        setBatch((prev) => prev ?? recent[0] ?? null);
        // Fetched together rather than one after the next; `allSettled` so one
        // failed batch costs its own verdicts, not the other four's. `adoptRuns`
        // keeps the newest run per case whatever order they arrive in, and
        // applying the responses oldest-first preserves how ties resolved when
        // these were serial.
        const lists = await Promise.allSettled(
          recent.map((b) => api.listTestRuns(b.test_case_batch_job_id)),
        );
        if (cancelled) return;
        for (const result of lists.reverse()) {
          if (result.status === "fulfilled") adoptRuns(result.value.items);
        }
      })
      // Tolerated the way the batch list above already is: this only backfills
      // the "Last result" column, and the tests themselves loaded fine — an
      // error banner over a populated table would name the wrong failure.
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [llmId, agentId, adoptRuns]);

  // Poll while a batch is running. Keyed on the batch id + status so it stops
  // as soon as the backend reports `complete`.
  const batchId = batch?.test_case_batch_job_id;
  const running = batch?.status === "in_progress";
  const [stalled, setStalled] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if (!batchId || !running) return;
    let cancelled = false;
    // A batch orphaned by an API restart never reaches `complete`, so give up
    // rather than poll the same row until the tab is closed.
    const deadline = Date.now() + POLL_DEADLINE_MS;
    const stop = () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
    const tick = async () => {
      if (Date.now() > deadline) {
        stop();
        setStalled(true);
        return;
      }
      try {
        const [head, runList] = await Promise.all([
          api.getBatchTest(batchId),
          api.listTestRuns(batchId),
        ]);
        if (cancelled) return;
        adoptRuns(runList.items);
        setBatch(head);
      } catch {
        // A transient poll failure is not worth surfacing; the next tick retries.
      }
    };
    setStalled(false);
    pollRef.current = setInterval(() => void tick(), POLL_MS);
    void tick();
    return () => {
      cancelled = true;
      stop();
    };
  }, [batchId, running, adoptRuns]);

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const runCases = async (ids: string[]) => {
    if (!llmId || ids.length === 0 || busy) return;
    setBusy("run");
    setError(null);
    try {
      const created = await api.createBatchTest({
        llm_id: llmId,
        agent_id: agentId,
        test_case_definition_ids: ids,
      });
      setBatch(created);
      // Show every case in the batch as queued right away, so the table reacts
      // before the first poll lands.
      const runList = await api.listTestRuns(created.test_case_batch_job_id);
      adoptRuns(runList.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start the run");
    } finally {
      setBusy(null);
    }
  };

  const generate = async () => {
    if (busy) return;
    setBusy("generate");
    setError(null);
    try {
      const res = await api.generateTestCases({ agent_id: agentId, count: GENERATE_COUNT });
      setCases((prev) => [...res.items, ...prev]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to generate test cases");
    } finally {
      setBusy(null);
    }
  };

  const saveCase = async (draft: TestCaseDraft) => {
    if (!llmId) return;
    if (editing) {
      const updated = await api.updateTestCase(editing.test_case_definition_id, draft);
      setCases((prev) =>
        prev.map((c) =>
          c.test_case_definition_id === updated.test_case_definition_id ? updated : c,
        ),
      );
    } else {
      const created = await api.createTestCase({ ...draft, llm_id: llmId });
      setCases((prev) => [created, ...prev]);
    }
  };

  const remove = async (testCase: RawTestCase) => {
    setConfirmDelete(null);
    try {
      await api.deleteTestCase(testCase.test_case_definition_id);
      setCases((prev) =>
        prev.filter((c) => c.test_case_definition_id !== testCase.test_case_definition_id),
      );
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(testCase.test_case_definition_id);
        return next;
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete the test case");
    }
  };

  if (!llm) {
    return (
      <div className="flex grow items-center justify-center rounded-xl border border-line bg-card">
        <p className="max-w-sm text-center text-[13px] text-sub">
          Simulation testing is available for prompt-based agents. This agent uses a conversation
          flow.
        </p>
      </div>
    );
  }

  const openRun = openCaseId ? runs[openCaseId] : null;
  const allIds = cases.map((c) => c.test_case_definition_id);
  const selectedIds = allIds.filter((id) => selected.has(id));

  // Summarize the verdicts the table is showing, not the last batch: running
  // one case re-runs one batch, and "Last run: 1/1 passed" over a table with
  // two red rows reads as a green suite.
  const graded = allIds
    .map((id) => runs[id]?.status)
    .filter((s) => s === "pass" || s === "fail" || s === "error");
  const passed = graded.filter((s) => s === "pass").length;

  return (
    <>
      <div className="flex min-w-[520px] flex-1 flex-col overflow-hidden rounded-xl border border-line bg-card">
        <div className="flex items-center gap-2 border-b border-line px-4 py-3">
          <h2 className="text-[15px] font-semibold">Test cases</h2>
          {graded.length > 0 && (
            <Badge tone={passed < graded.length ? "red" : "green"}>
              {passed}/{graded.length} passing
            </Badge>
          )}
          {running && (
            <Badge tone="gray">
              <Loader2 className="size-3 animate-spin" /> Running {batch?.total_count}
            </Badge>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" onClick={() => void generate()} disabled={busy !== null}>
              {busy === "generate" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Sparkles className="size-3.5" />
              )}
              {busy === "generate" ? "Writing tests…" : "Generate tests"}
            </Button>
            <Button
              size="sm"
              onClick={() => {
                setEditing(null);
                setModalOpen(true);
              }}
            >
              <Plus className="size-3.5" /> New
            </Button>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void runCases(selectedIds.length ? selectedIds : allIds)}
              disabled={busy !== null || cases.length === 0}
            >
              {busy === "run" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Play className="size-3.5" />
              )}
              {selectedIds.length ? `Run ${selectedIds.length}` : "Run all"}
            </Button>
          </div>
        </div>

        {dirty && (
          <p className="flex items-center gap-1.5 border-b border-line bg-amber-50 px-4 py-2 text-xs text-amber-700">
            <TriangleAlert className="size-3.5 shrink-0" />
            Runs use the last saved prompt — save the agent to test your current edits.
          </p>
        )}
        {stalled && (
          <p className="flex items-center gap-1.5 border-b border-line bg-amber-50 px-4 py-2 text-xs text-amber-700">
            <TriangleAlert className="size-3.5 shrink-0" />
            This run stopped reporting progress — reload the page, or run the cases again.
          </p>
        )}
        {error && (
          <p className="border-b border-line bg-red-50 px-4 py-2 text-xs text-bad">{error}</p>
        )}

        <div className="min-h-0 grow overflow-y-auto">
          {loading ? (
            <p className="py-16 text-center text-[13px] text-sub">Loading test cases…</p>
          ) : cases.length === 0 ? (
            <EmptyState
              icon={FlaskConical}
              title="No test cases yet"
              description="Write a scenario by hand, or let the agent read its own prompt and functions and draft a suite for itself."
              action={
                <Button variant="primary" onClick={() => void generate()} disabled={busy !== null}>
                  <Sparkles className="size-3.5" />
                  {busy === "generate" ? "Writing tests…" : "Generate tests"}
                </Button>
              }
            />
          ) : (
            <table className="w-full text-[13px]">
              <thead className="sticky top-0 bg-card text-xs text-sub">
                <tr className="border-b border-line">
                  <th className="w-9 py-2 pl-4">
                    <input
                      type="checkbox"
                      aria-label="Select all test cases"
                      checked={selectedIds.length === allIds.length && allIds.length > 0}
                      onChange={(e) =>
                        setSelected(e.target.checked ? new Set(allIds) : new Set())
                      }
                      className="cursor-pointer"
                    />
                  </th>
                  <th className="py-2 text-left font-medium">Name</th>
                  <th className="py-2 text-left font-medium">Scenario</th>
                  <th className="py-2 text-left font-medium">Criteria</th>
                  <th className="py-2 text-left font-medium">Last result</th>
                  <th className="w-20 py-2 pr-4" />
                </tr>
              </thead>
              <tbody>
                {cases.map((c) => {
                  const run = runs[c.test_case_definition_id];
                  return (
                    <tr
                      key={c.test_case_definition_id}
                      className="border-b border-line/70 hover:bg-app/60"
                    >
                      <td className="py-2.5 pl-4">
                        <input
                          type="checkbox"
                          aria-label={`Select ${c.name}`}
                          checked={selected.has(c.test_case_definition_id)}
                          onChange={() => toggle(c.test_case_definition_id)}
                          className="cursor-pointer"
                        />
                      </td>
                      <td className="max-w-48 py-2.5 pr-3">
                        <button
                          onClick={() => run && setOpenCaseId(c.test_case_definition_id)}
                          disabled={!run}
                          className="truncate text-left font-medium enabled:hover:text-accent-deep enabled:cursor-pointer disabled:cursor-default"
                          title={run ? "View the last run" : c.name}
                        >
                          {c.name}
                        </button>
                        {c.source === "generated" && (
                          <Badge tone="purple" className="ml-1.5">
                            auto
                          </Badge>
                        )}
                      </td>
                      <td className="max-w-72 truncate py-2.5 pr-3 text-sub" title={c.user_prompt}>
                        {c.user_prompt}
                      </td>
                      <td className="py-2.5 pr-3 text-sub">{c.metrics.length}</td>
                      <td className="py-2.5 pr-3">
                        {run ? (
                          <RunStatusBadge status={run.status} />
                        ) : (
                          <span className="text-faint">—</span>
                        )}
                      </td>
                      <td className="py-2.5 pr-4">
                        <div className="flex items-center justify-end gap-0.5">
                          <button
                            onClick={() => void runCases([c.test_case_definition_id])}
                            disabled={busy !== null}
                            aria-label={`Run ${c.name}`}
                            title="Run this test"
                            className="rounded-md p-1.5 text-sub hover:bg-app hover:text-ink disabled:opacity-50 cursor-pointer"
                          >
                            <Play className="size-3.5" />
                          </button>
                          <button
                            onClick={() => {
                              setEditing(c);
                              setModalOpen(true);
                            }}
                            aria-label={`Edit ${c.name}`}
                            className="rounded-md p-1.5 text-sub hover:bg-app hover:text-ink cursor-pointer"
                          >
                            <Pencil className="size-3.5" />
                          </button>
                          <button
                            onClick={() => setConfirmDelete(c)}
                            aria-label={`Delete ${c.name}`}
                            className="rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
                          >
                            <Trash2 className="size-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {/* Manual testing lives beside the automated suite, in the same slot the
          Create tab's settings occupy so switching tabs doesn't shift things. */}
      <div
        className={`${SIDE_PANEL_WIDTH} overflow-hidden rounded-xl border border-line bg-card`}
      >
        <TestPanel agentId={agentId} agentVersion={agentVersion} />
      </div>

      <TestCaseModal
        open={modalOpen}
        initial={editing}
        toolNames={toolNames}
        promptVariableNames={promptVariableNames}
        agentDefaultVariables={agentDefaultVariables}
        onClose={() => setModalOpen(false)}
        onSave={saveCase}
      />
      {openRun && <RunDrawer run={openRun} onClose={() => setOpenCaseId(null)} />}
      <Modal
        open={confirmDelete !== null}
        onClose={() => setConfirmDelete(null)}
        title="Delete test case"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={() => confirmDelete && void remove(confirmDelete)}
            >
              Delete
            </Button>
          </>
        }
      >
        <p className="text-[13px] text-sub">
          This deletes{" "}
          <span className="font-medium text-ink">{confirmDelete?.name}</span>. Past runs of it stay
          readable.
        </p>
      </Modal>
    </>
  );
}
