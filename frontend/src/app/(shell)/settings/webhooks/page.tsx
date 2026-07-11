"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Button from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Field";
import StatusDot from "@/components/ui/StatusDot";
import { api } from "@/lib/api";
import type { WebhookDelivery } from "@/lib/types";
import { formatCallTime, truncateId } from "@/lib/utils";
import { useEffect, useState } from "react";

export default function WebhooksPage() {
  const [url, setUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [deliveriesLoading, setDeliveriesLoading] = useState(true);

  useEffect(() => {
    api
      .getWorkspace()
      .then((ws) => setUrl(ws.webhook_url ?? ""))
      .catch(() => {}); // backend banner covers unreachable
    api
      .listWebhookDeliveries()
      .then(setDeliveries)
      .catch(() => {})
      .finally(() => setDeliveriesLoading(false));
  }, []);

  const save = async () => {
    setSaving(true);
    setSaveState("idle");
    setSaveError(null);
    try {
      const ws = await api.updateWorkspace({ webhook_url: url.trim() || null });
      setUrl(ws.webhook_url ?? "");
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2000);
    } catch (e) {
      setSaveState("error");
      setSaveError(e instanceof Error ? e.message : "Failed to save webhook URL");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Webhooks" />
        <div className="space-y-4">
          <SettingsCard
            title="Workspace Webhook URL"
            description="Receives call_started, call_ended and call_analyzed events for every agent in this workspace."
          >
            <div className="flex items-center gap-2">
              <TextInput
                placeholder="https://api.example.com/hooks/architeq"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
              <Button disabled title="Not available yet">
                Test
              </Button>
              <Button variant="primary" onClick={save} disabled={saving}>
                {saving ? "Saving…" : saveState === "saved" ? "Saved" : "Save"}
              </Button>
            </div>
            {saveState === "error" && saveError && (
              <p className="mt-2 text-[12.5px] text-bad">{saveError}</p>
            )}
          </SettingsCard>

          <SettingsCard title="Recent Deliveries">
            {deliveriesLoading && (
              <p className="py-6 text-center text-[13px] text-sub">Loading deliveries…</p>
            )}
            {!deliveriesLoading && deliveries.length === 0 && (
              <p className="py-6 text-center text-[13px] text-sub">
                No webhook deliveries yet. They appear here after calls trigger your webhook.
              </p>
            )}
            {!deliveriesLoading && deliveries.length > 0 && (
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b border-line text-[12.5px] text-sub">
                    <th className="py-2 pr-3 font-medium">Event</th>
                    <th className="px-3 py-2 font-medium">Delivery ID</th>
                    <th className="px-3 py-2 font-medium">Status</th>
                    <th className="px-3 py-2 text-right font-medium">Time</th>
                  </tr>
                </thead>
                <tbody>
                  {deliveries.map((d) => (
                    <tr key={d.delivery_id} className="border-b border-line/70 last:border-b-0">
                      <td className="py-2.5 pr-3 font-mono text-[12px]">{d.event}</td>
                      <td className="px-3 py-2.5 font-mono text-[12px] text-sub">
                        {truncateId(d.delivery_id, 12)}
                      </td>
                      <td className="px-3 py-2.5">
                        <StatusDot
                          color={d.status > 0 && d.status < 300 ? "green" : "red"}
                          label={d.status > 0 ? String(d.status) : "no response"}
                        />
                      </td>
                      <td className="px-3 py-2.5 text-right text-[12.5px] text-sub">
                        {formatCallTime(d.timestamp)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </SettingsCard>
        </div>
      </div>
    </div>
  );
}
