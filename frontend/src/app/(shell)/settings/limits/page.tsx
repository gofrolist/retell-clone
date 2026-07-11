"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Button from "@/components/ui/Button";
import Toggle from "@/components/ui/Toggle";

// Static replica of the Retell limits page — no backend for these knobs yet,
// so every control is disabled instead of pretending to persist.
export default function LimitsPage() {
  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Limits" />
        <div className="space-y-4">
          <SettingsCard
            title="Concurrent Calls Limit"
            description="Maximum calls your workspace can run at the same time."
            right={<span className="text-2xl font-semibold tabular-nums">20</span>}
          >
            <div className="flex items-center gap-2">
              <Button size="sm" disabled title="Not available yet">
                Adjust
              </Button>
              <Button size="sm" variant="ghost" disabled title="Not available yet">
                Reserve Inbound Capacity
              </Button>
            </div>
          </SettingsCard>

          <SettingsCard
            title="Concurrency Burst"
            description="Temporarily exceed your concurrency limit during spikes (billed per burst minute)."
            right={
              <span title="Not available yet">
                <Toggle checked={false} disabled />
              </span>
            }
          />

          <SettingsCard
            title="Conductor Messages"
            description="Allow Conductor to send proactive optimization messages for your agents."
            right={
              <span title="Not available yet">
                <Toggle checked={false} disabled />
              </span>
            }
          />

          <SettingsCard
            title="LLM Token Limit"
            description="Maximum tokens per LLM request across all agents."
            right={<span className="text-2xl font-semibold tabular-nums">32768</span>}
          />

          <SettingsCard
            title="Outbound Calls Per Second"
            description="Dialing rate limits per telephony provider."
          >
            <div className="divide-y divide-line rounded-lg border border-line">
              {[
                ["Telnyx CPS", "1"],
                ["Twilio CPS", "1"],
                ["Custom Telephony CPS", "1"],
              ].map(([name, value]) => (
                <div key={name} className="flex items-center justify-between px-3 py-2.5">
                  <span className="text-[13px]">{name}</span>
                  <span className="flex items-center gap-3">
                    <span className="text-[13.5px] font-semibold tabular-nums">{value}</span>
                    <Button size="sm" disabled title="Not available yet">
                      Adjust Limit
                    </Button>
                  </span>
                </div>
              ))}
            </div>
          </SettingsCard>
        </div>
      </div>
    </div>
  );
}
