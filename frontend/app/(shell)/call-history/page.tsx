"use client";

import CallDrawer from "@/components/calls/CallDrawer";
import CallsTable from "@/components/calls/CallsTable";
import Button from "@/components/ui/Button";
import Pagination from "@/components/ui/Pagination";
import { CheckboxRow } from "@/components/ui/RadioRow";
import Select from "@/components/ui/Select";
import { api, type ListCallsFilter } from "@/lib/api";
import type { Agent, Call } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  Calendar,
  ChevronDown,
  History,
  ListFilter,
  Settings2,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const CALL_STATUSES = ["registered", "ongoing", "ended", "error", "not_connected"];
const SENTIMENTS = ["Positive", "Neutral", "Negative", "Unknown"];
const DIRECTIONS = ["inbound", "outbound"];

function toggleValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function FilterSection({
  title,
  options,
  selected,
  onToggle,
}: {
  title: string;
  options: { value: string; label: string }[];
  selected: string[];
  onToggle: (value: string) => void;
}) {
  return (
    <div>
      <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-faint">
        {title}
      </div>
      <div className="max-h-40 overflow-y-auto">
        {options.map((o) => (
          <CheckboxRow
            key={o.value}
            checked={selected.includes(o.value)}
            onChange={() => onToggle(o.value)}
            label={o.label}
          />
        ))}
      </div>
    </div>
  );
}

export default function CallHistoryPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [calls, setCalls] = useState<Call[]>([]);
  const [selected, setSelected] = useState<Call | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  // filters
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [sentiments, setSentiments] = useState<string[]>([]);
  const [directions, setDirections] = useState<string[]>([]);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
  const [dateOpen, setDateOpen] = useState(false);

  // cursor pagination: `stack` holds the pagination_key of each previous page
  // (undefined for page 1) so Back can rewind; `pagKey` is the current page's
  // key and `nextKey` comes from the latest response.
  const [pageSize, setPageSize] = useState(50);
  const [stack, setStack] = useState<(string | undefined)[]>([]);
  const [pagKey, setPagKey] = useState<string | undefined>(undefined);
  const [nextKey, setNextKey] = useState<string | undefined>(undefined);
  const page = stack.length + 1;

  const resetPaging = useCallback(() => {
    setStack([]);
    setPagKey(undefined);
  }, []);

  useEffect(() => {
    api.listAgents().then(setAgents).catch(() => setAgents([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const fc: ListCallsFilter = {};
    if (agentIds.length) fc.agent_id = agentIds;
    if (statuses.length) fc.call_status = statuses;
    if (sentiments.length) fc.user_sentiment = sentiments;
    if (directions.length) fc.direction = directions;
    if (dateFrom || dateTo) {
      fc.start_timestamp = {
        ...(dateFrom ? { lower_threshold: new Date(`${dateFrom}T00:00:00`).getTime() } : {}),
        ...(dateTo ? { upper_threshold: new Date(`${dateTo}T23:59:59.999`).getTime() } : {}),
      };
    }

    api
      .listCalls({
        limit: pageSize,
        ...(pagKey ? { pagination_key: pagKey } : {}),
        ...(Object.keys(fc).length ? { filter_criteria: fc } : {}),
        sort_order: "descending",
      })
      .then((res) => {
        if (cancelled) return;
        setCalls(res.calls);
        setNextKey(res.pagination_key);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load calls");
        setCalls([]);
        setNextKey(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentIds, statuses, sentiments, directions, dateFrom, dateTo, pageSize, pagKey, reloadTick]);

  const onPage = useCallback(
    (p: number) => {
      if (p === page + 1 && nextKey) {
        setStack((s) => [...s, pagKey]);
        setPagKey(nextKey);
      } else if (p < page) {
        setPagKey(stack[p - 1]);
        setStack((s) => s.slice(0, p - 1));
      }
    },
    [page, nextKey, pagKey, stack],
  );

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

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") navigate(1);
      if (e.key === "ArrowUp") navigate(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, navigate]);

  const onCallUpdated = useCallback((updated: Call) => {
    setCalls((cs) => cs.map((c) => (c.call_id === updated.call_id ? updated : c)));
  }, []);

  const activeFilters =
    agentIds.length + statuses.length + sentiments.length + directions.length;
  const totalPages = nextKey ? page + 1 : page;
  const dateLabel =
    dateFrom || dateTo
      ? `${dateFrom ? formatDate(new Date(`${dateFrom}T00:00:00`).getTime()) : "…"} – ${
          dateTo ? formatDate(new Date(`${dateTo}T00:00:00`).getTime()) : "…"
        }`
      : "Date Range";

  return (
    <div className="flex h-full flex-col px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <History className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Call History</h1>
      </div>

      <div className="mb-3 flex items-center gap-2">
        <div className="relative">
          <Button
            onClick={() => {
              setDateOpen((v) => !v);
              setFilterOpen(false);
            }}
          >
            <Calendar className="size-3.5" />
            {dateLabel}
          </Button>
          {dateOpen && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setDateOpen(false)} />
              <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-xl border border-line bg-white p-3 shadow-lg">
                <div className="space-y-2.5">
                  <label className="block text-[13px]">
                    <span className="mb-1 block font-medium">From</span>
                    <input
                      type="date"
                      value={dateFrom}
                      onChange={(e) => {
                        setDateFrom(e.target.value);
                        resetPaging();
                      }}
                      className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none focus:border-accent"
                    />
                  </label>
                  <label className="block text-[13px]">
                    <span className="mb-1 block font-medium">To</span>
                    <input
                      type="date"
                      value={dateTo}
                      onChange={(e) => {
                        setDateTo(e.target.value);
                        resetPaging();
                      }}
                      className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none focus:border-accent"
                    />
                  </label>
                  <div className="flex justify-end">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setDateFrom("");
                        setDateTo("");
                        resetPaging();
                      }}
                    >
                      Clear
                    </Button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="relative">
          <Button
            onClick={() => {
              setFilterOpen((v) => !v);
              setDateOpen(false);
            }}
          >
            <ListFilter className="size-3.5" />
            Filter
            {activeFilters > 0 && (
              <span className="ml-0.5 rounded-full bg-ink px-1.5 text-[11px] font-semibold text-white">
                {activeFilters}
              </span>
            )}
          </Button>
          {filterOpen && (
            <>
              <div className="fixed inset-0 z-20" onClick={() => setFilterOpen(false)} />
              <div className="absolute left-0 top-full z-30 mt-1 w-80 space-y-3 rounded-xl border border-line bg-white p-3 shadow-lg">
                <FilterSection
                  title="Agent"
                  options={agents.map((a) => ({ value: a.agent_id, label: a.agent_name }))}
                  selected={agentIds}
                  onToggle={(v) => {
                    setAgentIds((l) => toggleValue(l, v));
                    resetPaging();
                  }}
                />
                <FilterSection
                  title="Call Status"
                  options={CALL_STATUSES.map((s) => ({ value: s, label: s }))}
                  selected={statuses}
                  onToggle={(v) => {
                    setStatuses((l) => toggleValue(l, v));
                    resetPaging();
                  }}
                />
                <FilterSection
                  title="User Sentiment"
                  options={SENTIMENTS.map((s) => ({ value: s, label: s }))}
                  selected={sentiments}
                  onToggle={(v) => {
                    setSentiments((l) => toggleValue(l, v));
                    resetPaging();
                  }}
                />
                <FilterSection
                  title="Direction"
                  options={DIRECTIONS.map((d) => ({ value: d, label: d }))}
                  selected={directions}
                  onToggle={(v) => {
                    setDirections((l) => toggleValue(l, v));
                    resetPaging();
                  }}
                />
                <div className="flex justify-end border-t border-line pt-2">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={activeFilters === 0}
                    onClick={() => {
                      setAgentIds([]);
                      setStatuses([]);
                      setSentiments([]);
                      setDirections([]);
                      resetPaging();
                    }}
                  >
                    Clear all
                  </Button>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          <Button variant="ghost" aria-label="Column settings" disabled title="Not available yet">
            <Settings2 className="size-4" />
          </Button>
          <Button variant="ghost" aria-label="AI insights" disabled title="Not available yet">
            <Sparkles className="size-4" />
          </Button>
          <Button variant="primary" disabled title="Not available yet">
            Actions
            <ChevronDown className="size-3.5" />
          </Button>
        </div>
      </div>

      <div className="min-h-0 grow overflow-auto rounded-t-lg border border-line border-b-0">
        {error ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 py-16 text-center">
            <p className="text-[13px] text-bad">{error}</p>
            <Button size="sm" onClick={() => setReloadTick((t) => t + 1)}>
              Retry
            </Button>
          </div>
        ) : loading ? (
          <p className="py-16 text-center text-[13px] text-sub">Loading calls…</p>
        ) : calls.length === 0 ? (
          <p className="py-16 text-center text-[13px] text-sub">
            {activeFilters > 0 || dateFrom || dateTo
              ? "No calls match the current filters."
              : "No calls yet."}
          </p>
        ) : (
          <CallsTable calls={calls} selectedId={selected?.call_id} onSelect={setSelected} />
        )}
      </div>

      <Pagination
        page={page}
        totalPages={totalPages}
        onPage={onPage}
        summary={`Page ${page} • ${calls.length} session${calls.length === 1 ? "" : "s"} on this page`}
        pageSizeControl={
          <Select
            value={String(pageSize)}
            onChange={(v) => {
              setPageSize(Number(v));
              resetPaging();
            }}
            options={[
              { value: "25", label: "25 / page" },
              { value: "50", label: "50 / page" },
              { value: "100", label: "100 / page" },
            ]}
          />
        }
      />

      {selected && (
        <CallDrawer
          call={selected}
          onClose={() => setSelected(null)}
          onNavigate={navigate}
          onUpdated={onCallUpdated}
        />
      )}
    </div>
  );
}
