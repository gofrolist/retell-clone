"use client";

import Transcript from "@/components/calls/Transcript";
import Badge from "@/components/ui/Badge";
import CopyId from "@/components/ui/CopyId";
import { uiTranscriptFromRaw, type MetricResult, type RawTestRun } from "@/lib/api";
import { Check, Loader2, X } from "lucide-react";
import { useMemo } from "react";
import { RunStatusBadge } from "./runStatus";

/** One list of pass/fail verdicts. Renders nothing when the case had none of
 *  this kind, so a purely-asserted case shows no empty "Success criteria". */
function Verdicts({
  title,
  caption,
  results,
}: {
  title: string;
  caption: string;
  results: MetricResult[];
}) {
  if (results.length === 0) return null;
  return (
    <section>
      <h3 className="mb-1.5 text-[13px] font-semibold text-sub">
        {title} <span className="font-normal text-faint">— {caption}</span>
      </h3>
      <ul className="divide-y divide-line/70 rounded-lg border border-line">
        {results.map((result, i) => (
          <li key={i} className="flex gap-2.5 px-3 py-2.5">
            <span
              className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full ${
                result.passed ? "bg-green-100 text-green-700" : "bg-red-100 text-bad"
              }`}
            >
              {result.passed ? <Check className="size-3" /> : <X className="size-3" />}
            </span>
            <div className="min-w-0 text-[13px]">
              <p className="font-medium">{result.metric}</p>
              {result.explanation && <p className="mt-0.5 text-sub">{result.explanation}</p>}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Full result of one simulation run: verdict, per-criterion grades, transcript. */
export default function RunDrawer({ run, onClose }: { run: RawTestRun; onClose: () => void }) {
  const transcript = useMemo(
    () => uiTranscriptFromRaw(run.transcript_snapshot?.messages ?? []),
    [run.transcript_snapshot],
  );
  const snapshot = run.test_case_definition_snapshot ?? {};
  const pending = run.status === "pending" || run.status === "in_progress";
  const script = snapshot.script ?? [];
  // `_verdict` builds this list as judged criteria first, then assertions, in
  // the order the case declared them — so the counts split it back apart. Shown
  // separately because a red criterion and a red assertion are not the same
  // news: one is a model's opinion of the transcript, the other is not.
  const results = run.metric_results ?? [];
  const judged = results.slice(0, snapshot.metrics?.length ?? 0);
  const asserted = results.slice(snapshot.metrics?.length ?? 0);

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/25"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="flex h-full w-full max-w-2xl flex-col bg-white shadow-2xl">
        <div className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 className="truncate text-[15px] font-semibold">
              {snapshot.name ?? "Simulation run"}
            </h2>
            <div className="mt-1 flex items-center gap-2">
              <RunStatusBadge status={run.status} />
              <CopyId value={run.test_case_job_id} className="text-[12px]" />
            </div>
          </div>
          <button
            onClick={onClose}
            className="ml-auto rounded-md p-1 text-sub hover:bg-app cursor-pointer"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="grow space-y-5 overflow-y-auto px-5 py-4">
          {pending && (
            <p className="flex items-center gap-2 rounded-lg border border-line bg-app px-3 py-2 text-[13px] text-sub">
              <Loader2 className="size-3.5 animate-spin" />
              This run is still going. Results appear as soon as the call is graded.
            </p>
          )}

          {run.status === "error" && run.result_explanation && (
            <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
              {run.result_explanation}
            </div>
          )}

          {script.length > 0 ? (
            <section>
              <h3 className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold text-sub">
                Caller script
                <Badge tone="blue">{script.length} turns</Badge>
              </h3>
              <ol className="divide-y divide-line/70 rounded-lg border border-line">
                {script.map((line, i) => (
                  <li key={i} className="flex gap-2.5 px-3 py-1.5 text-[13px]">
                    <span className="w-4 shrink-0 text-right text-faint">{i + 1}</span>
                    <span className="min-w-0">{line}</span>
                  </li>
                ))}
              </ol>
            </section>
          ) : (
            snapshot.user_prompt && (
              <section>
                <h3 className="mb-1.5 text-[13px] font-semibold text-sub">Scenario</h3>
                <p className="rounded-lg border border-line bg-app/50 px-3 py-2 text-[13px] whitespace-pre-wrap">
                  {snapshot.user_prompt}
                </p>
              </section>
            )
          )}

          <Verdicts title="Success criteria" caption="graded by a model" results={judged} />
          <Verdicts title="Assertions" caption="graded by code" results={asserted} />

          <section>
            <h3 className="mb-1.5 flex items-center gap-2 text-[13px] font-semibold text-sub">
              Simulated call
              {transcript.length > 0 && <Badge tone="gray">{transcript.length} turns</Badge>}
            </h3>
            {transcript.length > 0 ? (
              <Transcript turns={transcript} />
            ) : (
              <p className="text-[13px] text-sub">
                No transcript was recorded for this run.
              </p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
