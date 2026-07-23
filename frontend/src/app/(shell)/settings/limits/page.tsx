"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Button from "@/components/ui/Button";
import LoadError from "@/components/ui/LoadError";
import Modal from "@/components/ui/Modal";
import Toggle from "@/components/ui/Toggle";
import { api, type Workspace, type WorkspaceSettings } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

interface AdjustTarget {
  title: string;
  description: string;
  value: number;
  min: number;
  max: number;
  save: (value: number) => Partial<WorkspaceSettings>;
}

const CPS_PROVIDERS: { key: keyof WorkspaceSettings["cps_limits"]; label: string }[] = [
  { key: "telnyx", label: "Telnyx CPS" },
  { key: "twilio", label: "Twilio CPS" },
  { key: "custom_telephony", label: "Custom Telephony CPS" },
];

export default function LimitsPage() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [concurrencyLimit, setConcurrencyLimit] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [adjust, setAdjust] = useState<AdjustTarget | null>(null);
  const [adjustValue, setAdjustValue] = useState("");

  const load = useCallback(() => {
    setError(null);
    Promise.all([api.getWorkspace(), api.getConcurrency()])
      .then(([ws, conc]) => {
        setWorkspace(ws);
        setConcurrencyLimit(conc.concurrency_limit);
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load workspace limits"),
      );
  }, []);

  useEffect(load, [load]);

  const persist = useCallback(
    async (settings: Partial<WorkspaceSettings>) => {
      setSaving(true);
      setSaveError(null);
      try {
        const ws = await api.updateWorkspace({ settings });
        setWorkspace(ws);
        setConcurrencyLimit(null);
        const conc = await api.getConcurrency();
        setConcurrencyLimit(conc.concurrency_limit);
        return true;
      } catch (e: unknown) {
        setSaveError(e instanceof Error ? e.message : "Failed to save");
        return false;
      } finally {
        setSaving(false);
      }
    },
    [],
  );

  const openAdjust = (target: AdjustTarget) => {
    setSaveError(null);
    setAdjustValue(String(target.value));
    setAdjust(target);
  };

  if (error) {
    return (
      <div className="h-full overflow-y-auto px-8 py-6">
        <div className="mx-auto max-w-2xl">
          <SettingsPageHeader title="Limits" />
          <p className="text-[13px]">
            <LoadError error={error} onRetry={load} />
          </p>
        </div>
      </div>
    );
  }

  const s = workspace?.settings;

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Limits" />
        {saveError && !adjust && (
          <p className="mb-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
            {saveError}
          </p>
        )}
        <div className="space-y-4">
          <SettingsCard
            title="Concurrent Calls Limit"
            description="Maximum calls your workspace can run at the same time."
            right={
              <span className="text-2xl font-semibold tabular-nums">
                {concurrencyLimit ?? "—"}
              </span>
            }
          >
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                disabled={!s}
                onClick={() =>
                  s &&
                  openAdjust({
                    title: "Adjust Concurrent Calls Limit",
                    description:
                      "Additional concurrency on top of the base limit of 20. Applies immediately to new calls.",
                    value: s.purchased_concurrency,
                    min: 0,
                    max: 100,
                    save: (v) => ({ purchased_concurrency: v }),
                  })
                }
              >
                Adjust
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!s}
                onClick={() =>
                  s &&
                  openAdjust({
                    title: "Reserve Inbound Capacity",
                    description:
                      "Concurrency slots held back for inbound calls. Outbound and web calls can't use reserved slots.",
                    value: s.reserved_inbound_concurrency,
                    min: 0,
                    // At least one slot must stay outbound (the backend
                    // rejects reserving the full limit).
                    max: Math.max(0, (concurrencyLimit ?? 20) - 1),
                    save: (v) => ({ reserved_inbound_concurrency: v }),
                  })
                }
              >
                Reserve Inbound Capacity
              </Button>
            </div>
            {s !== undefined && s.reserved_inbound_concurrency > 0 && (
              <p className="mt-2 text-[12.5px] text-sub">
                {s.reserved_inbound_concurrency} slot
                {s.reserved_inbound_concurrency === 1 ? "" : "s"} reserved for inbound calls.
              </p>
            )}
          </SettingsCard>

          <SettingsCard
            title="Concurrency Burst"
            description="Temporarily exceed your concurrency limit during spikes (up to 3× your limit)."
            right={
              <Toggle
                checked={s?.concurrency_burst_enabled ?? false}
                disabled={!s || saving}
                onChange={(v) => persist({ concurrency_burst_enabled: v })}
              />
            }
          />

          <SettingsCard
            title="Conductor Messages"
            description="Allow Conductor to send proactive optimization messages for your agents."
            right={
              <Toggle
                checked={s?.conductor_messages_enabled ?? false}
                disabled={!s || saving}
                onChange={(v) => persist({ conductor_messages_enabled: v })}
              />
            }
          />

          <SettingsCard
            title="LLM Token Limit"
            description="Maximum tokens per LLM request across all agents."
            right={
              <span className="text-2xl font-semibold tabular-nums">
                {s?.llm_token_limit ?? "—"}
              </span>
            }
          >
            <Button
              size="sm"
              disabled={!s}
              onClick={() =>
                s &&
                openAdjust({
                  title: "Adjust LLM Token Limit",
                  description: "Maximum tokens per LLM request (1,024 – 131,072).",
                  value: s.llm_token_limit,
                  min: 1024,
                  max: 131072,
                  save: (v) => ({ llm_token_limit: v }),
                })
              }
            >
              Adjust
            </Button>
          </SettingsCard>

          <SettingsCard
            title="Outbound Calls Per Second"
            description="Dialing rate limits per telephony provider."
          >
            <div className="divide-y divide-line rounded-lg border border-line">
              {CPS_PROVIDERS.map(({ key, label }) => (
                <div key={key} className="flex items-center justify-between px-3 py-2.5">
                  <span className="text-[13px]">{label}</span>
                  <span className="flex items-center gap-3">
                    <span className="text-[13.5px] font-semibold tabular-nums">
                      {s?.cps_limits[key] ?? "—"}
                    </span>
                    <Button
                      size="sm"
                      disabled={!s}
                      onClick={() =>
                        s &&
                        openAdjust({
                          title: `Adjust ${label}`,
                          description: "Outbound dial rate for this provider (1 – 100 CPS).",
                          value: s.cps_limits[key],
                          min: 1,
                          max: 100,
                          save: (v) => ({
                            cps_limits: { ...s.cps_limits, [key]: v },
                          }),
                        })
                      }
                    >
                      Adjust Limit
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          </SettingsCard>
        </div>
      </div>

      <Modal
        open={adjust !== null}
        onClose={() => setAdjust(null)}
        title={adjust?.title}
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAdjust(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={saving}
              onClick={async () => {
                if (!adjust) return;
                const v = Number(adjustValue);
                if (!Number.isInteger(v) || v < adjust.min || v > adjust.max) {
                  setSaveError(
                    `Enter a whole number between ${adjust.min} and ${adjust.max}`,
                  );
                  return;
                }
                if (await persist(adjust.save(v))) setAdjust(null);
              }}
            >
              {saving ? "Saving…" : "Save"}
            </Button>
          </>
        }
      >
        {adjust && (
          <div className="space-y-3">
            <p className="text-[13px] text-sub">{adjust.description}</p>
            <input
              type="number"
              min={adjust.min}
              max={adjust.max}
              value={adjustValue}
              onChange={(e) => setAdjustValue(e.target.value)}
              className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] tabular-nums outline-none focus:border-accent"
            />
            {saveError && <p className="text-[13px] text-bad">{saveError}</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}
