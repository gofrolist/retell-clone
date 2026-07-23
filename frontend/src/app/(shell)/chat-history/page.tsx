"use client";

import ChatDrawer from "@/components/chats/ChatDrawer";
import ChatsTable from "@/components/chats/ChatsTable";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import Pagination from "@/components/ui/Pagination";
import { CheckboxRow } from "@/components/ui/RadioRow";
import Select from "@/components/ui/Select";
import { api, type RawChat } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { ListFilter, MessageSquareText } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

const CHAT_STATUSES = ["ongoing", "ended", "error"];

function toggleValue(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export default function ChatHistoryPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [chats, setChats] = useState<RawChat[]>([]);
  const [selected, setSelected] = useState<RawChat | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);

  // filters
  const [agentIds, setAgentIds] = useState<string[]>([]);
  const [statuses, setStatuses] = useState<string[]>([]);
  const [filterOpen, setFilterOpen] = useState(false);

  // cursor pagination, same scheme as Call History: `stack` remembers each
  // previous page's key so Back can rewind.
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

  const agentNames = useMemo(
    () => Object.fromEntries(agents.map((a) => [a.agent_id, a.agent_name])),
    [agents],
  );

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const fc: { agent_id?: string[]; chat_status?: string[] } = {};
    if (agentIds.length) fc.agent_id = agentIds;
    if (statuses.length) fc.chat_status = statuses;

    api
      .listChats({
        limit: pageSize,
        ...(pagKey ? { pagination_key: pagKey } : {}),
        ...(Object.keys(fc).length ? { filter_criteria: fc } : {}),
        sort_order: "descending",
      })
      .then((res) => {
        if (cancelled) return;
        setChats(res.items);
        setNextKey(res.has_more && res.next_pagination_key ? res.next_pagination_key : undefined);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load chats");
        setChats([]);
        setNextKey(undefined);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentIds, statuses, pageSize, pagKey, reloadTick]);

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
        const idx = chats.findIndex((c) => c.chat_id === cur.chat_id);
        return chats[idx + dir] ?? cur;
      });
    },
    [chats],
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

  const activeFilters = agentIds.length + statuses.length;
  const totalPages = nextKey ? page + 1 : page;
  const showEmptyState = !loading && !error && chats.length === 0 && activeFilters === 0 && page === 1;

  return (
    <div className="flex h-full flex-col px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <MessageSquareText className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Chat History</h1>
      </div>

      {showEmptyState ? (
        <EmptyState
          icon={MessageSquareText}
          title="No chat sessions yet"
          description="Chat sessions created via the Test LLM panel or the chat API will appear here."
        />
      ) : (
        <>
          <div className="mb-3 flex items-center gap-2">
            <div className="relative">
              <Button onClick={() => setFilterOpen((v) => !v)}>
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
                    <div>
                      <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-faint">
                        Agent
                      </div>
                      <div className="max-h-40 overflow-y-auto">
                        {agents.map((a) => (
                          <CheckboxRow
                            key={a.agent_id}
                            checked={agentIds.includes(a.agent_id)}
                            onChange={() => {
                              setAgentIds((l) => toggleValue(l, a.agent_id));
                              resetPaging();
                            }}
                            label={a.agent_name}
                          />
                        ))}
                      </div>
                    </div>
                    <div>
                      <div className="mb-0.5 text-xs font-semibold uppercase tracking-wide text-faint">
                        Session Status
                      </div>
                      {CHAT_STATUSES.map((s) => (
                        <CheckboxRow
                          key={s}
                          checked={statuses.includes(s)}
                          onChange={() => {
                            setStatuses((l) => toggleValue(l, s));
                            resetPaging();
                          }}
                          label={s}
                        />
                      ))}
                    </div>
                    <div className="flex justify-end border-t border-line pt-2">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled={activeFilters === 0}
                        onClick={() => {
                          setAgentIds([]);
                          setStatuses([]);
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
              <p className="py-16 text-center text-[13px] text-sub">Loading chats…</p>
            ) : chats.length === 0 ? (
              <p className="py-16 text-center text-[13px] text-sub">
                No chats match the current filters.
              </p>
            ) : (
              <ChatsTable
                chats={chats}
                agentNames={agentNames}
                selectedId={selected?.chat_id}
                onSelect={setSelected}
              />
            )}
          </div>

          <Pagination
            page={page}
            totalPages={totalPages}
            onPage={onPage}
            summary={`Page ${page} • ${chats.length} session${chats.length === 1 ? "" : "s"} on this page`}
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
        </>
      )}

      {selected && (
        <ChatDrawer
          chat={selected}
          agentNames={agentNames}
          onClose={() => setSelected(null)}
          onNavigate={navigate}
        />
      )}
    </div>
  );
}
