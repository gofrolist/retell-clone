"use client";

import CreateAlertModal from "@/components/alerting/CreateAlertModal";
import Button from "@/components/ui/Button";
import LoadError from "@/components/ui/LoadError";
import RowMenu from "@/components/ui/RowMenu";
import Toggle from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { BellRing, Plus } from "lucide-react";
import { useState } from "react";

export default function AlertingPage() {
  const { data, setData: setAlerts, loading, error, setError, reload } = useApiData(
    () => api.listAlerts(),
  );
  const alerts = data ?? [];
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Alert | null>(null);

  const toggle = async (id: string, v: boolean) => {
    const prev = alerts;
    setAlerts((cur) => (cur ?? []).map((a) => (a.alert_id === id ? { ...a, enabled: v } : a)));
    try {
      await api.updateAlert(id, { enabled: v });
    } catch {
      setAlerts(prev); // revert optimistic update
    }
  };

  const deleteAlert = async (id: string) => {
    try {
      await api.deleteAlert(id);
      setAlerts((cur) => (cur ?? []).filter((a) => a.alert_id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete alert");
    }
  };

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BellRing className="size-4.5 text-sub" strokeWidth={1.8} />
          <h1 className="text-[17px] font-semibold">Alerting</h1>
        </div>
        <Button
          variant="primary"
          onClick={() => {
            setEditing(null);
            setModalOpen(true);
          }}
        >
          <Plus className="size-3.5" />
          Create Alert
        </Button>
      </div>

      <div className="divide-y divide-line rounded-xl border border-line bg-white shadow-sm">
        {loading && (
          <div className="px-4 py-10 text-center text-[13px] text-sub">Loading alerts…</div>
        )}
        {!loading && error && (
          <div className="px-4 py-10 text-center text-[13px]">
            <LoadError error={error} onRetry={reload} />
          </div>
        )}
        {!loading && !error && alerts.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-sub">
            No alerts yet. Create one to get notified when a call metric crosses a threshold.
          </div>
        )}
        {alerts.map((a) => (
          <div key={a.alert_id} className="flex items-center gap-3 px-4 py-3">
            <Toggle checked={a.enabled} onChange={(v) => toggle(a.alert_id, v)} />
            <div className="min-w-0 grow">
              <div className="truncate text-[13.5px] font-medium">{a.name}</div>
              <div className="text-xs text-sub">
                Every {a.check_every_min} min · last {a.lookback_min} min · {a.metric}{" "}
                {a.condition} {a.threshold}
                {a.notify_emails.length > 0 && <> · notify {a.notify_emails.join(", ")}</>}
                {a.webhook_url ? " + webhook" : ""}
              </div>
            </div>
            <Button
              size="sm"
              onClick={() => {
                setEditing(a);
                setModalOpen(true);
              }}
            >
              Edit
            </Button>
            <RowMenu onDelete={() => deleteAlert(a.alert_id)} />
          </div>
        ))}
      </div>

      <CreateAlertModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        editing={editing}
        onSaved={reload}
      />
    </div>
  );
}
