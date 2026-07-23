"use client";

import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import StatusDot from "@/components/ui/StatusDot";
import { api, type SystemComponent, type Workspace } from "@/lib/api";
import {
  AudioWaveform,
  Blocks,
  CalendarCheck,
  MessageSquareText,
  Phone,
  Server,
  Sparkles,
  Webhook,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

type Status = "connected" | "not_configured" | "degraded" | "per_agent";

const STATUS_UI: Record<Status, { color: "green" | "gray" | "orange" | "blue"; label: string }> = {
  connected: { color: "green", label: "Connected" },
  not_configured: { color: "gray", label: "Not configured" },
  degraded: { color: "orange", label: "Degraded" },
  per_agent: { color: "blue", label: "Configured per agent" },
};

interface IntegrationCard {
  key: string;
  name: string;
  description: string;
  icon: typeof Phone;
  status: Status;
  detail?: string;
  action: { label: string; href: string };
}

function statusFromComponent(c: SystemComponent | undefined): Status {
  if (!c) return "not_configured";
  if (c.status === "operational") return "connected";
  if (c.status === "degraded") return "degraded";
  return "not_configured";
}

export default function IntegrationsPage() {
  const [components, setComponents] = useState<SystemComponent[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    Promise.allSettled([api.getSystemStatus(), api.getWorkspace()]).then(([status, ws]) => {
      if (status.status === "fulfilled") setComponents(status.value.components);
      if (ws.status === "fulfilled") setWorkspace(ws.value);
      setLoaded(true);
    });
  }, []);

  const byKey = Object.fromEntries(components.map((c) => [c.key, c]));

  const cards: IntegrationCard[] = [
    {
      key: "telephony",
      name: "Telnyx (SIP telephony)",
      description: "Outbound and inbound PSTN calling through the platform SIP trunk.",
      icon: Phone,
      status: statusFromComponent(byKey["telephony"]),
      detail: byKey["telephony"]?.detail,
      action: { label: "Phone Numbers", href: "/phone-numbers" },
    },
    {
      key: "livekit",
      name: "LiveKit",
      description: "Realtime voice infrastructure that carries every call's audio.",
      icon: AudioWaveform,
      status: statusFromComponent(byKey["livekit"]),
      detail: byKey["livekit"]?.detail,
      action: { label: "Live Monitoring", href: "/live-monitoring" },
    },
    {
      key: "gemini",
      name: "Google Gemini",
      description: "LLM powering agent responses, chat, and post-call analysis.",
      icon: Sparkles,
      status: statusFromComponent(byKey["llm"]),
      detail: byKey["llm"]?.detail,
      action: { label: "Agents", href: "/agents" },
    },
    {
      key: "webhooks",
      name: "Webhooks",
      description: "Signed call_started / call_ended / call_analyzed events to your backend.",
      icon: Webhook,
      status: workspace?.webhook_url ? statusFromComponent(byKey["webhooks"]) : "not_configured",
      detail: workspace?.webhook_url ?? "No workspace webhook URL set",
      action: { label: "Configure", href: "/settings/webhooks" },
    },
    {
      key: "cal",
      name: "Cal.com",
      description:
        "Appointment booking via check_availability_cal / book_appointment_cal agent functions.",
      icon: CalendarCheck,
      status: "per_agent",
      action: { label: "Agent functions", href: "/agents" },
    },
    {
      key: "mcp",
      name: "MCP servers",
      description: "Connect Model Context Protocol servers as tools on an agent's LLM.",
      icon: Server,
      status: "per_agent",
      action: { label: "Agent editor", href: "/agents" },
    },
    {
      key: "sms",
      name: "Telnyx SMS",
      description: "Send SMS mid-call via the send_sms agent function (needs a Telnyx API key).",
      icon: MessageSquareText,
      status: "per_agent",
      action: { label: "Agent functions", href: "/agents" },
    },
  ];

  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <div className="mb-1 flex items-center gap-2">
        <Blocks className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Integrations</h1>
      </div>
      <p className="mb-5 text-[13px] text-sub">
        Services connected to this workspace. Status reflects live platform checks.
      </p>

      {!loaded ? (
        <p className="py-16 text-center text-[13px] text-sub">Checking integrations…</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 pb-8 md:grid-cols-2 xl:grid-cols-3">
          {cards.map((card) => {
            const ui = STATUS_UI[card.status];
            return (
              <div
                key={card.key}
                className="flex flex-col rounded-xl border border-line bg-white p-4 shadow-sm"
              >
                <div className="flex items-start justify-between gap-3">
                  <span className="flex size-9 items-center justify-center rounded-lg border border-line bg-app">
                    <card.icon className="size-4.5 text-sub" strokeWidth={1.8} />
                  </span>
                  {card.status === "per_agent" ? (
                    <Badge tone="blue">{ui.label}</Badge>
                  ) : (
                    <StatusDot color={ui.color} label={ui.label} />
                  )}
                </div>
                <div className="mt-3 text-[13.5px] font-semibold">{card.name}</div>
                <p className="mt-1 grow text-[12.5px] leading-relaxed text-sub">
                  {card.description}
                </p>
                {card.detail && (
                  <p className="mt-1 truncate text-[11.5px] text-faint" title={card.detail}>
                    {card.detail}
                  </p>
                )}
                <div className="mt-3">
                  <Link href={card.action.href}>
                    <Button size="sm">{card.action.label}</Button>
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
