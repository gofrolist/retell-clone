"use client";

import CreateAlertModal from "@/components/alerting/CreateAlertModal";
import Button from "@/components/ui/Button";
import Toggle from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";
import { BellRing, MoreHorizontal, Plus } from "lucide-react";
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

export default function AlertingPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Alert | null>(null);

  const load = () => {
    api
      .listAlerts()
      .then((list) => {
        setAlerts(list);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load alerts"))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const toggle = async (id: string, v: boolean) => {
    const prev = alerts;
    setAlerts((cur) => cur.map((a) => (a.alert_id === id ? { ...a, enabled: v } : a)));
    try {
      await api.updateAlert(id, { enabled: v });
    } catch {
      setAlerts(prev); // revert optimistic update
    }
  };

  const deleteAlert = async (id: string) => {
    try {
      await api.deleteAlert(id);
      setAlerts((cur) => cur.filter((a) => a.alert_id !== id));
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
        onSaved={load}
      />
    </div>
  );
}
