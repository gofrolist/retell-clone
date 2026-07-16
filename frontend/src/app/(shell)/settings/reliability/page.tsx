"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import StatusDot from "@/components/ui/StatusDot";
import Toggle from "@/components/ui/Toggle";

// Static replica of the Retell reliability page — no backend for these
// settings yet, so the toggles are disabled instead of faking persistence.
export default function ReliabilityPage() {
  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Reliability" />
        <div className="space-y-4">
          <SettingsCard
            title="Service Status"
            description="Live status of Arhiteq platform components."
          >
            <div className="divide-y divide-line rounded-lg border border-line">
              {["Call Engine", "LLM Gateway", "Telephony (Twilio)", "Telephony (Telnyx)", "Webhooks"].map(
                (name) => (
                  <div key={name} className="flex items-center justify-between px-3 py-2.5">
                    <span className="text-[13px]">{name}</span>
                    <StatusDot color="green" label="Operational" />
                  </div>
                ),
              )}
            </div>
          </SettingsCard>

          <SettingsCard
            title="LLM Failover"
            description="Automatically fail over to a backup model when the primary provider degrades."
            right={
              <span title="Not available yet">
                <Toggle checked={false} disabled />
              </span>
            }
          />

          <SettingsCard
            title="Automatic Call Retry"
            description="Retry outbound calls that fail due to carrier errors (up to 2 retries)."
            right={
              <span title="Not available yet">
                <Toggle checked={false} disabled />
              </span>
            }
          />
        </div>
      </div>
    </div>
  );
}
