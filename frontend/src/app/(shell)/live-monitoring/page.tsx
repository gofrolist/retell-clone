"use client";

import CallDrawer from "@/components/calls/CallDrawer";
import CopyId from "@/components/ui/CopyId";
import EmptyState from "@/components/ui/EmptyState";
import StatusDot from "@/components/ui/StatusDot";
import { api } from "@/lib/api";
import type { Call } from "@/lib/types";
import { cn, formatCallTime, formatDuration, pressableProps, truncateId } from "@/lib/utils";
import { Activity, RadioTower } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const POLL_MS = 5000;

interface Concurrency {
  current_concurrency: number;
  concurrency_limit: number;
}

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-xl border border-line bg-card px-4 py-3">
      <div className="text-[13px] text-sub">{label}</div>
      <div className="mt-1 text-[22px] font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-[12px] text-faint">{sub}</div>}
    </div>
  );
}

/** Duration cell that ticks every second for calls still in progress. */
function LiveDuration({ start }: { start: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!start) return <span className="text-sub">-</span>;
  return <span className="tabular-nums">{formatDuration(now - start)}</span>;
}

export default function LiveMonitoringPage() {
  const [calls, setCalls] = useState<Call[]>([]);
  const [concurrency, setConcurrency] = useState<Concurrency | null>(null);
  const [selected, setSelected] = useState<Call | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRefresh, setLastRefresh] = useState<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [res, conc] = await Promise.all([
        api.listCalls({
          limit: 100,
          filter_criteria: { call_status: ["registered", "ongoing"] },
          sort_order: "descending",
        }),
        api.getConcurrency(),
      ]);
      setCalls(res.calls);
      setConcurrency(conc);
      setError(null);
      setLastRefresh(Date.now());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load live calls");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [refresh]);

  const navigate = useCallback(
    (dir: 1 | -1) => {
      setSelected((cur) => {
        if (!cur) return cur;
        const idx = calls.findIndex((c) => c.call_id === cur.call_id);
        return calls[idx + dir] ?? cur;
      });
    },
    [calls],
  );

  const used = concurrency
    ? `${concurrency.current_concurrency} / ${concurrency.concurrency_limit}`
    : "—";

  return (
    <div className="flex h-full flex-col px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <RadioTower className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Live Monitoring</h1>
        <span className="ml-auto flex items-center gap-1.5 text-[12px] text-faint">
          <Activity className="size-3.5 text-ok" />
          {lastRefresh ? `Auto-refreshing every ${POLL_MS / 1000}s` : "Connecting…"}
        </span>
      </div>

      {error && (
        <p className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
          {error}
        </p>
      )}

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        <StatTile
          label="Live Calls"
          value={loaded ? String(calls.length) : "—"}
          sub="Dialing + in progress"
        />
        <StatTile label="Concurrency Used" value={used} sub="Across this workspace" />
        <StatTile
          label="Concurrency Remaining"
          value={
            concurrency
              ? String(Math.max(0, concurrency.concurrency_limit - concurrency.current_concurrency))
              : "—"
          }
        />
      </div>

      {loaded && !error && calls.length === 0 ? (
        <EmptyState
          icon={RadioTower}
          title="No live calls right now"
          description="Calls that are dialing or in progress appear here in real time. This page refreshes automatically."
        />
      ) : (
        <div className="min-h-0 grow overflow-auto rounded-t-lg border border-line border-b-0">
          <table className="w-full min-w-[860px] text-left">
            <thead className="sticky top-0 z-[1] bg-card">
              <tr className="border-b border-line text-[13px] text-sub">
                {["Started", "Duration", "Status", "Agent", "Channel", "From", "To", "Call ID"].map(
                  (h) => (
                    <th key={h} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr
                  key={c.call_id}
                  {...pressableProps(`Live call ${c.call_id}`, () => setSelected(c))}
                  className={cn(
                    "cursor-pointer border-b border-line/70 transition-colors",
                    selected?.call_id === c.call_id ? "bg-app" : "hover:bg-app/60",
                  )}
                >
                  <td className="whitespace-nowrap py-3 pl-4 pr-3">
                    {c.start_timestamp ? formatCallTime(c.start_timestamp) : "Dialing…"}
                  </td>
                  <td className="px-3 py-3">
                    {c.call_status === "ongoing" && c.start_timestamp ? (
                      <LiveDuration start={c.start_timestamp} />
                    ) : (
                      <span className="text-sub">-</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">
                    <StatusDot
                      color={c.call_status === "ongoing" ? "blue" : "gray"}
                      label={c.call_status === "ongoing" ? "ongoing" : "dialing"}
                    />
                  </td>
                  <td className="whitespace-nowrap px-3 py-3">{c.agent_name}</td>
                  <td className="px-3 py-3 text-sub">{c.channel_type}</td>
                  <td className="whitespace-nowrap px-3 py-3 tabular-nums text-sub">
                    {c.from_number || "-"}
                  </td>
                  <td className="whitespace-nowrap px-3 py-3 tabular-nums text-sub">
                    {c.to_number || "-"}
                  </td>
                  <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                    <CopyId value={c.call_id} display={truncateId(c.call_id, 18)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {selected && (
        <CallDrawer call={selected} onClose={() => setSelected(null)} onNavigate={navigate} />
      )}
    </div>
  );
}
