"use client";

import CreateCohortModal from "@/components/qa/CreateCohortModal";
import Button from "@/components/ui/Button";
import { api } from "@/lib/api";
import type { Agent, QaCohort } from "@/lib/types";
import { MoreHorizontal, Plus, ScanSearch, TrendingUp } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function RowMenu({ onDelete }: { onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded-md p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
        aria-label="More"
      >
        <MoreHorizontal className="size-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-32 rounded-lg border border-line bg-white p-1 shadow-lg">
          <button
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[13px] text-bad hover:bg-app cursor-pointer"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function MetricCard({ label, value, suffix }: { label: string; value: string; suffix?: string }) {
  return (
    <div className="rounded-xl border border-line bg-white p-4 shadow-sm">
      <div className="text-[13px] text-sub">{label}</div>
      <div className="mt-1 flex items-end justify-between gap-3">
        <span className="text-3xl font-semibold tabular-nums tracking-tight">
          {value}
          {suffix && <span className="text-lg text-sub">{suffix}</span>}
        </span>
      </div>
    </div>
  );
}

export default function QualityAssurancePage() {
  const [cohorts, setCohorts] = useState<QaCohort[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = () => {
    api
      .listCohorts()
      .then((list) => {
        setCohorts(list);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load cohorts"))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    api.listAgents().then(setAgents).catch(() => {});
  }, []);

  const deleteCohort = async (id: string) => {
    try {
      await api.deleteCohort(id);
      setCohorts((cur) => cur.filter((c) => c.cohort_id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete cohort");
    }
  };

  const agentName = (id: string) => agents.find((a) => a.agent_id === id)?.agent_name ?? id;

  // The scoring pipeline isn't built yet — the backend reports 0 for these.
  // Show "—" instead of pretending we measured something.
  const withData = cohorts.filter((c) => c.transfer_success_rate > 0);
  const avgSuccess = withData.length
    ? (withData.reduce((s, c) => s + c.transfer_success_rate, 0) / withData.length).toFixed(0)
    : null;
  const withWait = cohorts.filter((c) => c.transfer_wait_time_s > 0);
  const avgWait = withWait.length
    ? (withWait.reduce((s, c) => s + c.transfer_wait_time_s, 0) / withWait.length).toFixed(1)
    : null;

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ScanSearch className="size-4.5 text-sub" strokeWidth={1.8} />
          <h1 className="text-[17px] font-semibold">AI Quality Assurance</h1>
        </div>
        <Button variant="primary" onClick={() => setCreateOpen(true)}>
          <Plus className="size-3.5" />
          Create QA
        </Button>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <MetricCard
          label="Transfer Success Rate"
          value={avgSuccess ?? "—"}
          suffix={avgSuccess ? "%" : undefined}
        />
        <MetricCard
          label="Transfer Wait Time"
          value={avgWait ?? "—"}
          suffix={avgWait ? "s" : undefined}
        />
      </div>

      <h2 className="mt-6 mb-2 text-[14px] font-semibold">Cohorts</h2>
      <div className="divide-y divide-line rounded-xl border border-line bg-white shadow-sm">
        {loading && (
          <div className="px-4 py-10 text-center text-[13px] text-sub">Loading cohorts…</div>
        )}
        {!loading && error && (
          <div className="px-4 py-10 text-center text-[13px]">
            <span className="text-bad">{error}</span>{" "}
            <button
              onClick={() => {
                setLoading(true);
                load();
              }}
              className="font-medium text-accent-deep hover:underline cursor-pointer"
            >
              Retry
            </button>
          </div>
        )}
        {!loading && !error && cohorts.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-sub">
            No QA cohorts yet. Create one to sample and score calls.
          </div>
        )}
        {cohorts.map((c) => (
          <div key={c.cohort_id} className="flex items-center gap-3 px-4 py-3">
            <span className="flex size-8 items-center justify-center rounded-lg border border-line bg-app">
              <TrendingUp className="size-4 text-sub" />
            </span>
            <div className="min-w-0 grow">
              <div className="truncate text-[13.5px] font-medium">{c.name}</div>
              <div className="truncate text-xs text-sub">
                {c.agents.length > 0 ? c.agents.map(agentName).join(", ") : "All agents"} ·
                sampling {c.sampling_pct}% · max {c.weekly_max}/week
              </div>
            </div>
            <span className="text-[13px] tabular-nums text-sub">
              {c.transfer_success_rate > 0 ? `${c.transfer_success_rate}% success` : "— success"}
            </span>
            <RowMenu onDelete={() => deleteCohort(c.cohort_id)} />
          </div>
        ))}
      </div>

      <CreateCohortModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        agents={agents}
        onCreated={load}
      />
    </div>
  );
}
