"use client";

import AudioPlayer, { type AudioMarker } from "./AudioPlayer";
import LiveDuration from "./LiveDuration";
import LiveTranscript from "./LiveTranscript";
import Transcript from "./Transcript";
import CopyId from "@/components/ui/CopyId";
import StatusDot from "@/components/ui/StatusDot";
import { UnderlineTabs } from "@/components/ui/Tabs";
import { api } from "@/lib/api";
import type { Call, DetailLog, TranscriptItem } from "@/lib/types";
import {
  formatCallTime,
  formatCost,
  formatDuration,
  formatDurationLong,
  truncateId,
} from "@/lib/utils";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  RefreshCw,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

// How long after a call ends to refetch it, so the post-call pipeline (which
// runs after finalize responds) has written recording, cost and analysis.
const ANALYSIS_SETTLE_MS = 4000;

function MetaItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 py-1 text-[13px]">
      <span className="w-28 shrink-0 text-sub">{label}</span>
      <span className="min-w-0">{children}</span>
    </div>
  );
}

function AnalysisRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b border-line/70 py-2 text-[13px] last:border-b-0">
      <span className="text-sub">{label}</span>
      <span>{children}</span>
    </div>
  );
}

function DataPanel({ vars }: { vars?: Record<string, string> }) {
  const entries = Object.entries(vars ?? {});
  if (entries.length === 0) {
    return (
      <p className="py-8 text-center text-[13px] text-sub">
        No extracted data available for this call.
      </p>
    );
  }
  return (
    <div>
      <h4 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-sub">
        <span className="font-mono text-faint">{"{ }"}</span> Dynamic Variables
      </h4>
      <dl className="rounded-lg border border-line bg-app/50 divide-y divide-line/70">
        {entries.map(([k, v]) => (
          <div key={k} className="px-3 py-2 text-[13px]">
            <dt className="font-mono text-[12px] text-sub">{k}</dt>
            <dd className="mt-0.5 break-words whitespace-pre-wrap">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function LogsPanel({ logs }: { logs?: DetailLog[] }) {
  if (!logs || logs.length === 0) {
    return (
      <p className="py-8 text-center text-[13px] text-sub">
        No detail logs available for this call.
      </p>
    );
  }
  const fmt = (ms: number) => {
    const d = new Date(ms);
    const p = (n: number, w = 2) => String(n).padStart(w, "0");
    return (
      `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
      `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
    );
  };
  return (
    <div className="space-y-1 font-mono text-[12px] leading-relaxed">
      {logs.map((l, i) => (
        <div key={i} className={l.level === "error" ? "text-bad" : "text-sub"}>
          <span className="text-faint">{fmt(l.time_ms)}</span> {l.message}
        </div>
      ))}
    </div>
  );
}

export default function CallDrawer({
  call,
  onClose,
  onNavigate,
  onUpdated,
  live = false,
}: {
  call: Call;
  onClose: () => void;
  onNavigate: (dir: 1 | -1) => void;
  onUpdated?: (call: Call) => void;
  /** Live Monitoring: follow the call over SSE instead of fetching it once. */
  live?: boolean;
}) {
  const [tab, setTab] = useState("transcription");
  // list rows can be partial — fetch the full call on open
  const [full, setFull] = useState<Call | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  // Move focus into the drawer on open, restore it to the opener on close.
  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    drawerRef.current?.focus();
    return () => previouslyFocused?.focus?.();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setFull(null);
    setError(null);

    if (live) {
      // The stream's first event is the full call, so there's no separate
      // fetch: every later event is the transcript filling in.
      const unsubscribe = api.streamCall(call.call_id, {
        onCall: (c) => setFull(c),
        onEnd: () => {
          // The call just ended. Recording, cost and analysis are written by
          // the post-call pipeline moments later, so pick them up once it has
          // had a chance to run rather than leaving the panel half-empty.
          setTimeout(() => {
            if (cancelled) return;
            api
              .getCall(call.call_id)
              .then((c) => !cancelled && setFull(c))
              .catch(() => {});
          }, ANALYSIS_SETTLE_MS);
        },
      });
      return () => {
        cancelled = true;
        unsubscribe();
      };
    }

    api
      .getCall(call.call_id)
      .then((c) => {
        if (!cancelled) setFull(c);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load call");
      });
    return () => {
      cancelled = true;
    };
  }, [call.call_id, live]);

  const c = full ?? call;
  const isLive = c.call_status === "ongoing" || c.call_status === "registered";

  // Timeline annotations: one dot per tool invocation (popup includes its
  // paired result) and per KB retrieval. Items without time_ms (calls
  // recorded before the worker stamped timestamps) get no marker, and every
  // timestamped invocation carries a tool_call_id, so pairing is a pure id
  // lookup — no name-based guessing.
  const markers = useMemo<AudioMarker[]>(() => {
    const items = c.transcript ?? [];
    const resultById = new Map<string, TranscriptItem>();
    for (const r of items) {
      if (r.role === "tool_result" && r.tool_call_id && !resultById.has(r.tool_call_id)) {
        resultById.set(r.tool_call_id, r);
      }
    }
    return items.flatMap((t): AudioMarker[] => {
      if (typeof t.time_ms !== "number") return [];
      if (t.role === "tool_invocation") {
        const result = t.tool_call_id ? resultById.get(t.tool_call_id) : undefined;
        const body = [`args: ${t.content}`, result ? `result: ${result.content}` : null]
          .filter(Boolean)
          .join("\n");
        return [{ time_ms: t.time_ms, kind: "tool", title: t.name ?? "tool call", body }];
      }
      if (t.role === "kb_retrieval") {
        return [{ time_ms: t.time_ms, kind: "kb", title: "Knowledge Base Retrieval" }];
      }
      return [];
    });
  }, [c.transcript]);

  async function rerun() {
    if (rerunning) return;
    setRerunning(true);
    setError(null);
    try {
      await api.rerunCallAnalysis(c.call_id);
      const updated = await api.getCall(c.call_id);
      setFull(updated);
      onUpdated?.(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to rerun analysis");
    } finally {
      setRerunning(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/25" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="Call details"
        tabIndex={-1}
        className="flex h-full w-full max-w-4xl bg-card shadow-2xl outline-none"
      >
        {/* main drawer column */}
        <div className="flex min-w-0 grow flex-col">
          <div className="flex items-center gap-2 border-b border-line px-5 py-3">
            <button onClick={onClose} className="rounded-md p-1 text-sub hover:bg-app cursor-pointer" aria-label="Close">
              <X className="size-4" />
            </button>
            <span className="text-[13px] text-sub">
              <kbd className="rounded border border-line bg-app px-1">↑</kbd>{" "}
              <kbd className="rounded border border-line bg-app px-1">↓</kbd> to navigate
            </span>
            <span className="ml-auto flex items-center gap-1">
              {!full && !error && <span className="text-[12px] text-faint">Loading…</span>}
              <button onClick={() => onNavigate(-1)} className="rounded-md p-1 text-sub hover:bg-app cursor-pointer" aria-label="Previous call">
                <ChevronUp className="size-4" />
              </button>
              <button onClick={() => onNavigate(1)} className="rounded-md p-1 text-sub hover:bg-app cursor-pointer" aria-label="Next call">
                <ChevronDown className="size-4" />
              </button>
            </span>
          </div>

          <div className="min-h-0 grow overflow-y-auto px-5 py-4">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold">
              {c.start_timestamp ? formatCallTime(c.start_timestamp) : "Dialing…"}
              {isLive && (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-ok/10 px-2 py-0.5 text-[11.5px] font-medium text-ok">
                  <span className="size-1.5 animate-pulse rounded-full bg-ok" />
                  {c.call_status === "ongoing" ? "Live" : "Dialing"}
                </span>
              )}
            </h2>

            {error && (
              <p className="mt-2 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
                {error}
              </p>
            )}

            <div className="mt-3">
              <MetaItem label="Agent">
                <span className="flex items-center gap-1.5">
                  {c.agent_name}
                  <CopyId value={c.agent_id} display={truncateId(c.agent_id, 14)} />
                </span>
              </MetaItem>
              <MetaItem label="Version">{c.agent_version}</MetaItem>
              <MetaItem label="Call ID">
                <CopyId value={c.call_id} display={truncateId(c.call_id, 26)} />
              </MetaItem>
              <MetaItem label="Phone Call">
                <span className="inline-flex items-center gap-1.5 tabular-nums">
                  {c.from_number} → {c.to_number}
                  <span className="inline-flex items-center gap-0.5 text-sub">
                    {c.direction === "outbound" ? (
                      <ArrowUpRight className="size-3.5" />
                    ) : (
                      <ArrowDownLeft className="size-3.5" />
                    )}
                    ({c.direction === "outbound" ? "Outbound" : "Inbound"})
                  </span>
                </span>
              </MetaItem>
              <MetaItem label="Duration">
                {isLive ? (
                  // No duration_ms until finalize — count from the answer.
                  <LiveDuration start={c.start_timestamp} />
                ) : (
                  <>
                    {formatCallTime(c.start_timestamp).split("·")[1] ?? "—"} -{" "}
                    {formatDuration(c.duration_ms)}{" "}
                    <span className="text-sub">({formatDurationLong(c.duration_ms)})</span>
                  </>
                )}
              </MetaItem>
              <MetaItem label="Cost">{formatCost(c.cost)}</MetaItem>
              <MetaItem label="LLM Token">
                <span className="tabular-nums">{c.llm_token_usage?.toLocaleString() ?? "-"}</span>
              </MetaItem>
            </div>

            {c.recording_url && /^https?:/i.test(c.recording_url) && (
              <div className="mt-4">
                <AudioPlayer
                  src={c.recording_url}
                  durationMs={c.duration_ms || 0}
                  markers={markers}
                />
              </div>
            )}

            {/* Analysis only exists after the call ends — the post-call
                pipeline writes it — so a live call goes straight to the
                transcript rather than showing a panel of "Unknown"s and a
                Rerun that has nothing to rerun. */}
            {!isLive && (
            <section className="mt-5">
              <div className="mb-1 flex items-center justify-between">
                <h3 className="text-[14px] font-semibold">Conversation Analysis</h3>
                <button
                  onClick={rerun}
                  disabled={rerunning}
                  className="inline-flex items-center gap-1 text-[13px] text-sub hover:text-ink cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <RefreshCw className={`size-3.5 ${rerunning ? "animate-spin" : ""}`} />
                  {rerunning ? "Rerunning…" : "Rerun"}
                </button>
              </div>
              <div>
                <AnalysisRow label="Call Successful">
                  <StatusDot
                    color={c.call_successful ? "green" : c.call_successful === false ? "red" : "gray"}
                    label={c.call_successful ? "Successful" : c.call_successful === false ? "Unsuccessful" : "Unknown"}
                  />
                </AnalysisRow>
                <AnalysisRow label="Call Status">
                  <StatusDot color={c.call_status === "ended" ? "gray" : "red"} label={c.call_status} />
                </AnalysisRow>
                <AnalysisRow label="User Sentiment">
                  <StatusDot
                    color={
                      c.user_sentiment === "Positive"
                        ? "green"
                        : c.user_sentiment === "Negative"
                          ? "red"
                          : c.user_sentiment === "Neutral"
                            ? "blue"
                            : "gray"
                    }
                    label={c.user_sentiment}
                  />
                </AnalysisRow>
                <AnalysisRow label="Disconnection Reason">
                  {c.disconnection_reason ? c.disconnection_reason.replace(/_/g, " ") : "-"}
                </AnalysisRow>
                <AnalysisRow label="End to End Latency">
                  <span className="tabular-nums">
                    {c.end_to_end_latency_ms ? `${c.end_to_end_latency_ms}ms` : "-"}
                  </span>
                </AnalysisRow>
              </div>
            </section>
            )}

            {c.call_summary && (
              <section className="mt-5">
                <h3 className="mb-1 text-[14px] font-semibold">Summary</h3>
                <p className="text-[13px] leading-relaxed text-sub">{c.call_summary}</p>
              </section>
            )}

            <section className="mt-5">
              <UnderlineTabs
                tabs={[
                  { key: "transcription", label: "Transcription" },
                  { key: "data", label: "Data" },
                  { key: "logs", label: "Detail Logs" },
                  { key: "pcap", label: "Packet Capture" },
                ]}
                active={tab}
                onChange={setTab}
              />
              <div className="pt-4 pb-6">
                {tab === "transcription" ? (
                  isLive ? (
                    <LiveTranscript turns={c.transcript ?? []} />
                  ) : (
                    <Transcript turns={c.transcript ?? []} />
                  )
                ) : tab === "data" ? (
                  <DataPanel vars={c.dynamic_variables} />
                ) : tab === "logs" ? (
                  <LogsPanel logs={c.detail_logs} />
                ) : (
                  <p className="py-8 text-center text-[13px] text-sub">
                    No packet capture available for this call.
                  </p>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
