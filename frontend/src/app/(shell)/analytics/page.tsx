"use client";

import DonutCard from "@/components/analytics/DonutCard";
import StatTile from "@/components/analytics/StatTile";
import { CallCountsArea, ConcurrencyBars, SeriesCard } from "@/components/analytics/TimeCharts";
import Button from "@/components/ui/Button";
import { CheckboxRow, RadioRow } from "@/components/ui/RadioRow";
import { UnderlineTabs } from "@/components/ui/Tabs";
import { api, type AnalyticsParams } from "@/lib/api";
import type { Agent, AnalyticsData, ChatAnalyticsData } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  Calendar,
  GitBranch,
  ListFilter,
  MessageSquare,
  MoreHorizontal,
  Phone,
  Plus,
  RefreshCw,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

const RANGE_PRESETS = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

// Extra charts the "+ Add Chart" menu can pin (persisted per browser).
const EXTRA_CHARTS = [
  { key: "calls_by_agent", label: "Calls by Agent" },
  { key: "cumulative_calls", label: "Cumulative Calls" },
] as const;
type ExtraChartKey = (typeof EXTRA_CHARTS)[number]["key"];
const EXTRA_CHARTS_LS_KEY = "arhiteq.analytics.extraCharts";

function Popover({
  open,
  onClose,
  children,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  wide?: boolean;
}) {
  if (!open) return null;
  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div
        className={`absolute left-0 top-full z-30 mt-1 ${wide ? "w-80" : "w-56"} space-y-1 rounded-xl border border-line bg-white p-2 shadow-lg`}
      >
        {children}
      </div>
    </>
  );
}

function toCsv(rows: (string | number)[][]): string {
  return rows.map((r) => r.map((v) => JSON.stringify(v)).join(",")).join("\n");
}

export default function AnalyticsPage() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [chatData, setChatData] = useState<ChatAnalyticsData | null>(null);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tab, setTab] = useState("call");
  const [error, setError] = useState<string | null>(null);

  // toolbar state
  const [days, setDays] = useState(30);
  const [customFrom, setCustomFrom] = useState("");
  const [customTo, setCustomTo] = useState("");
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [breakdown, setBreakdown] = useState<"none" | "agent" | "direction">("none");
  const [extraCharts, setExtraCharts] = useState<ExtraChartKey[]>([]);
  const [agentGroups, setAgentGroups] = useState<AnalyticsData["call_counts_groups"]>();
  const [openMenu, setOpenMenu] = useState<"date" | "filter" | "breakdown" | "add" | "more" | null>(
    null,
  );
  const [reloadTick, setReloadTick] = useState(0);

  useEffect(() => {
    api.listAgents().then(setAgents).catch(() => setAgents([]));
    try {
      const stored = JSON.parse(localStorage.getItem(EXTRA_CHARTS_LS_KEY) ?? "[]");
      if (Array.isArray(stored)) {
        setExtraCharts(
          stored.filter((k): k is ExtraChartKey => EXTRA_CHARTS.some((c) => c.key === k)),
        );
      }
    } catch {
      // corrupted localStorage: start clean
    }
  }, []);

  const params = useMemo((): AnalyticsParams => {
    const p: AnalyticsParams = {};
    if (customFrom && customTo) {
      p.start_ms = new Date(`${customFrom}T00:00:00`).getTime();
      p.end_ms = new Date(`${customTo}T00:00:00`).getTime();
    } else {
      p.days = days;
    }
    if (agentIds.length) p.agent_ids = agentIds;
    return p;
  }, [days, customFrom, customTo, agentIds]);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    const load =
      tab === "chat"
        ? api.getChatAnalytics(params).then((d) => !cancelled && setChatData(d))
        : api
            .getAnalytics({ ...params, ...(breakdown !== "none" ? { group_by: breakdown } : {}) })
            .then((d) => !cancelled && setData(d));
    load.catch((e: unknown) => {
      if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load analytics");
    });
    return () => {
      cancelled = true;
    };
  }, [tab, params, breakdown, reloadTick]);

  // "Calls by Agent" pinned chart needs the per-agent grouping regardless of
  // the breakdown selector.
  useEffect(() => {
    if (!extraCharts.includes("calls_by_agent")) return;
    let cancelled = false;
    api
      .getAnalytics({ ...params, group_by: "agent" })
      .then((d) => !cancelled && setAgentGroups(d.call_counts_groups))
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [extraCharts, params, reloadTick]);

  const persistExtraCharts = useCallback((next: ExtraChartKey[]) => {
    setExtraCharts(next);
    localStorage.setItem(EXTRA_CHARTS_LS_KEY, JSON.stringify(next));
  }, []);

  const dateLabel =
    customFrom && customTo
      ? `${formatDate(new Date(`${customFrom}T00:00:00`).getTime())} - ${formatDate(new Date(`${customTo}T00:00:00`).getTime())}`
      : (RANGE_PRESETS.find((p) => p.days === days)?.label ?? `Last ${days} days`);

  const exportCsv = () => {
    const series = tab === "chat" ? chatData?.chat_counts_series : data?.call_counts_series;
    if (!series) return;
    const blob = new Blob(
      [toCsv([["date", tab === "chat" ? "chats" : "calls"], ...series.map((p) => [p.date, p.value])])],
      { type: "text/csv" },
    );
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `arhiteq-${tab}-analytics.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const cumulative = useMemo(() => {
    if (!data) return [];
    let sum = 0;
    return data.call_counts_series.map((p) => ({ date: p.date, value: (sum += p.value) }));
  }, [data]);

  const agentNames = useMemo(
    () => Object.fromEntries(agents.map((a) => [a.agent_id, a.agent_name])),
    [agents],
  );

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <h1 className="text-[17px] font-semibold">Analytics</h1>

      <UnderlineTabs
        className="mt-3"
        tabs={[
          {
            key: "call",
            label: (
              <span className="inline-flex items-center gap-1.5">
                <Phone className="size-3.5" /> Call Dashboard
              </span>
            ),
          },
          {
            key: "chat",
            label: (
              <span className="inline-flex items-center gap-1.5">
                <MessageSquare className="size-3.5" /> Chat Dashboard
              </span>
            ),
          },
        ]}
        active={tab}
        onChange={setTab}
      />

      <div className="mt-4 flex items-center gap-2">
        <div className="relative">
          <Button onClick={() => setOpenMenu(openMenu === "date" ? null : "date")}>
            <Calendar className="size-3.5" />
            {dateLabel}
          </Button>
          <Popover open={openMenu === "date"} onClose={() => setOpenMenu(null)} wide>
            {RANGE_PRESETS.map((p) => (
              <RadioRow
                key={p.days}
                checked={!customFrom && !customTo && days === p.days}
                onSelect={() => {
                  setDays(p.days);
                  setCustomFrom("");
                  setCustomTo("");
                  setOpenMenu(null);
                }}
                label={p.label}
              />
            ))}
            <div className="border-t border-line px-1 pt-2">
              <div className="mb-1 text-xs font-semibold uppercase tracking-wide text-faint">
                Custom range
              </div>
              <div className="flex items-center gap-2 pb-1">
                <input
                  type="date"
                  value={customFrom}
                  onChange={(e) => setCustomFrom(e.target.value)}
                  className="h-8 w-full rounded-lg border border-line bg-white px-2 text-[12.5px] outline-none focus:border-accent"
                />
                <span className="text-sub">–</span>
                <input
                  type="date"
                  value={customTo}
                  onChange={(e) => setCustomTo(e.target.value)}
                  className="h-8 w-full rounded-lg border border-line bg-white px-2 text-[12.5px] outline-none focus:border-accent"
                />
              </div>
            </div>
          </Popover>
        </div>

        <div className="relative">
          <Button onClick={() => setOpenMenu(openMenu === "filter" ? null : "filter")}>
            <ListFilter className="size-3.5" />
            Filter
            {agentIds.length > 0 && (
              <span className="ml-0.5 rounded-full bg-ink px-1.5 text-[11px] font-semibold text-white">
                {agentIds.length}
              </span>
            )}
          </Button>
          <Popover open={openMenu === "filter"} onClose={() => setOpenMenu(null)} wide>
            <div className="mb-0.5 px-1 text-xs font-semibold uppercase tracking-wide text-faint">
              Agent
            </div>
            <div className="max-h-52 overflow-y-auto">
              {agents.map((a) => (
                <CheckboxRow
                  key={a.agent_id}
                  checked={agentIds.includes(a.agent_id)}
                  onChange={() =>
                    setAgentIds((l) =>
                      l.includes(a.agent_id)
                        ? l.filter((x) => x !== a.agent_id)
                        : [...l, a.agent_id],
                    )
                  }
                  label={a.agent_name}
                />
              ))}
              {agents.length === 0 && (
                <p className="px-1 py-2 text-[12.5px] text-sub">No agents yet.</p>
              )}
            </div>
            {agentIds.length > 0 && (
              <div className="flex justify-end border-t border-line pt-1.5">
                <Button size="sm" variant="ghost" onClick={() => setAgentIds([])}>
                  Clear
                </Button>
              </div>
            )}
          </Popover>
        </div>

        {tab === "call" && (
          <div className="relative">
            <Button onClick={() => setOpenMenu(openMenu === "breakdown" ? null : "breakdown")}>
              <GitBranch className="size-3.5" />
              Breakdown
              {breakdown !== "none" && (
                <span className="ml-0.5 rounded-full bg-ink px-1.5 text-[11px] font-semibold text-white">
                  1
                </span>
              )}
            </Button>
            <Popover open={openMenu === "breakdown"} onClose={() => setOpenMenu(null)}>
              {(
                [
                  ["none", "None"],
                  ["agent", "By Agent"],
                  ["direction", "By Direction"],
                ] as const
              ).map(([value, label]) => (
                <RadioRow
                  key={value}
                  checked={breakdown === value}
                  onSelect={() => {
                    setBreakdown(value);
                    setOpenMenu(null);
                  }}
                  label={label}
                />
              ))}
            </Popover>
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          {tab === "call" && (
            <div className="relative">
              <Button onClick={() => setOpenMenu(openMenu === "add" ? null : "add")}>
                <Plus className="size-3.5" />
                Add Chart
              </Button>
              <Popover open={openMenu === "add"} onClose={() => setOpenMenu(null)}>
                {EXTRA_CHARTS.map((c) => (
                  <CheckboxRow
                    key={c.key}
                    checked={extraCharts.includes(c.key)}
                    onChange={() =>
                      persistExtraCharts(
                        extraCharts.includes(c.key)
                          ? extraCharts.filter((k) => k !== c.key)
                          : [...extraCharts, c.key],
                      )
                    }
                    label={c.label}
                  />
                ))}
              </Popover>
            </div>
          )}
          <div className="relative">
            <Button
              variant="ghost"
              aria-label="More"
              onClick={() => setOpenMenu(openMenu === "more" ? null : "more")}
            >
              <MoreHorizontal className="size-4" />
            </Button>
            <Popover open={openMenu === "more"} onClose={() => setOpenMenu(null)}>
              <button
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                onClick={() => {
                  setReloadTick((t) => t + 1);
                  setOpenMenu(null);
                }}
              >
                <RefreshCw className="size-3.5 text-sub" /> Refresh
              </button>
              <button
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-app cursor-pointer"
                onClick={() => {
                  exportCsv();
                  setOpenMenu(null);
                }}
              >
                <Calendar className="size-3.5 text-sub" /> Export CSV
              </button>
            </Popover>
          </div>
        </div>
      </div>

      {error && (
        <p className="mt-4 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
          {error}
        </p>
      )}

      {data && tab === "call" && (
        <div className="mt-4 space-y-4 pb-8">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <StatTile label="Call Counts" value={String(data.call_counts)} />
            <StatTile label="Call Duration" value={`${data.avg_duration_s}s`} />
            <StatTile label="Call Latency" value={`${data.avg_latency_ms}ms`} />
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
            <CallCountsArea data={data.call_counts_series} />
            <ConcurrencyBars data={data.concurrency_series} />
          </div>

          {breakdown !== "none" && data.call_counts_groups && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {data.call_counts_groups.map((g) => (
                <SeriesCard
                  key={g.name}
                  title={g.name}
                  label="Call counts"
                  data={g.series}
                  height="h-40"
                />
              ))}
              {data.call_counts_groups.length === 0 && (
                <p className="py-8 text-center text-[13px] text-sub">
                  No calls in this window to break down.
                </p>
              )}
            </div>
          )}

          {extraCharts.length > 0 && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {extraCharts.includes("cumulative_calls") && (
                <SeriesCard title="Cumulative Calls" label="Total calls" data={cumulative} />
              )}
              {extraCharts.includes("calls_by_agent") && agentGroups && (
                <DonutCard
                  title="Calls by Agent"
                  data={agentGroups.map((g) => ({
                    name: g.name,
                    value: g.series.reduce((s, p) => s + p.value, 0),
                  }))}
                />
              )}
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <DonutCard title="Call Successful" data={data.call_successful} />
            <DonutCard title="Disconnection Reason" data={data.disconnection_reason} />
            <DonutCard title="User Sentiment" data={data.user_sentiment} />
            <DonutCard title="Phone inbound/outbound" data={data.phone_direction} />
          </div>
        </div>
      )}

      {tab === "chat" &&
        (chatData ? (
          chatData.chat_counts === 0 ? (
            <p className="py-24 text-center text-[13px] text-sub">
              No chats in this window. Chats created via the Test LLM panel or the chat API will
              populate this dashboard.
            </p>
          ) : (
            <div className="mt-4 space-y-4 pb-8">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                <StatTile label="Chat Counts" value={String(chatData.chat_counts)} />
                <StatTile label="Messages / Chat" value={String(chatData.avg_messages)} />
                <StatTile label="Chat Duration" value={`${chatData.avg_duration_s}s`} />
              </div>

              <div className="grid grid-cols-1 gap-4 lg:grid-cols-[2fr_1fr]">
                <SeriesCard
                  title="Chat Counts"
                  label="Chat counts"
                  data={chatData.chat_counts_series}
                />
                <SeriesCard
                  title="Messages"
                  label="Messages"
                  data={chatData.messages_series}
                  kind="bar"
                />
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <DonutCard title="Chat Status" data={chatData.chat_status} />
                <DonutCard
                  title="Chats by Agent"
                  data={chatData.chat_agent.map((d) => ({
                    name: agentNames[d.name] ?? d.name,
                    value: d.value,
                  }))}
                />
              </div>
            </div>
          )
        ) : (
          !error && <p className="py-24 text-center text-[13px] text-sub">Loading…</p>
        ))}
    </div>
  );
}
