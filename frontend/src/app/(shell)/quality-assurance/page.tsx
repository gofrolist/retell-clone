"use client";

import CreateCohortModal from "@/components/qa/CreateCohortModal";
import Button from "@/components/ui/Button";
import LoadError from "@/components/ui/LoadError";
import RowMenu from "@/components/ui/RowMenu";
import { api } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { Plus, ScanSearch, TrendingUp } from "lucide-react";
import { useEffect, useState } from "react";

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
  const { data, setData: setCohorts, loading, error, setError, reload } = useApiData(
    () => api.listCohorts(),
  );
  const cohorts = data ?? [];
  const [agents, setAgents] = useState<Agent[]>([]);
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    api.listAgents().then(setAgents).catch(() => {});
  }, []);

  const deleteCohort = async (id: string) => {
    try {
      await api.deleteCohort(id);
      setCohorts((cur) => (cur ?? []).filter((c) => c.cohort_id !== id));
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
            <LoadError error={error} onRetry={reload} />
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
        onCreated={reload}
      />
    </div>
  );
}
