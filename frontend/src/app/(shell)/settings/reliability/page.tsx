"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import LoadError from "@/components/ui/LoadError";
import StatusDot from "@/components/ui/StatusDot";
import Toggle from "@/components/ui/Toggle";
import { api, type SystemComponent, type Workspace } from "@/lib/api";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

const STATUS_UI: Record<SystemComponent["status"], { color: "green" | "orange" | "red" | "gray"; label: string }> = {
  operational: { color: "green", label: "Operational" },
  degraded: { color: "orange", label: "Degraded" },
  down: { color: "red", label: "Down" },
  not_configured: { color: "gray", label: "Not configured" },
};

export default function ReliabilityPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [components, setComponents] = useState<SystemComponent[] | null>(null);
  const [checkedAt, setCheckedAt] = useState<number | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const loadStatus = useCallback(() => {
    setChecking(true);
    api
      .getSystemStatus()
      .then((res) => {
        setComponents(res.components);
        setCheckedAt(res.checked_at_ms);
        setError(null);
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load system status"),
      )
      .finally(() => setChecking(false));
  }, []);

  useEffect(() => {
    loadStatus();
    api.getWorkspace().then(setWorkspace).catch(() => setWorkspace(null));
  }, [loadStatus]);

  const persist = async (settings: Parameters<typeof api.updateWorkspace>[0]["settings"]) => {
    setSaving(true);
    setSaveError(null);
    try {
      setWorkspace(await api.updateWorkspace({ settings }));
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const s = workspace?.settings;

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Reliability" />
        {saveError && (
          <p className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
            {saveError}
          </p>
        )}
        <div className="space-y-4">
          <SettingsCard
            title="Service Status"
            description="Live status of Arhiteq platform components."
            right={
              <button
                onClick={loadStatus}
                disabled={checking}
                className="inline-flex items-center gap-1 text-[13px] text-sub hover:text-ink cursor-pointer disabled:opacity-50"
              >
                <RefreshCw className={`size-3.5 ${checking ? "animate-spin" : ""}`} />
                {checking ? "Checking…" : "Refresh"}
              </button>
            }
          >
            {error ? (
              <p className="text-[13px]">
                <LoadError error={error} onRetry={loadStatus} />
              </p>
            ) : components === null ? (
              <p className="py-4 text-center text-[13px] text-sub">Checking components…</p>
            ) : (
              <>
                <div className="divide-y divide-line rounded-lg border border-line">
                  {components.map((c) => (
                    <div key={c.key} className="flex items-center justify-between px-3 py-2.5">
                      <span className="text-[13px]">
                        {c.name}
                        {c.detail && (
                          <span className="ml-2 text-[12px] text-faint">{c.detail}</span>
                        )}
                      </span>
                      <StatusDot
                        color={STATUS_UI[c.status].color}
                        label={STATUS_UI[c.status].label}
                      />
                    </div>
                  ))}
                </div>
                {checkedAt && (
                  <p className="mt-2 text-[12px] text-faint">
                    Last checked {new Date(checkedAt).toLocaleTimeString()}
                  </p>
                )}
              </>
            )}
          </SettingsCard>

          <SettingsCard
            title="LLM Failover"
            description="Automatically fail over to a backup model when the primary provider degrades."
            right={
              <Toggle
                checked={s?.llm_failover_enabled ?? false}
                disabled={!s || saving}
                onChange={(v) => persist({ llm_failover_enabled: v })}
              />
            }
          />

          <SettingsCard
            title="Automatic Call Retry"
            description="Retry outbound calls that fail due to carrier errors (up to 2 retries)."
            right={
              <Toggle
                checked={s?.auto_call_retry_enabled ?? false}
                disabled={!s || saving}
                onChange={(v) => persist({ auto_call_retry_enabled: v })}
              />
            }
          />
        </div>
      </div>
    </div>
  );
}
