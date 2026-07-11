"use client";

import Badge from "@/components/ui/Badge";
import { CheckboxRow } from "@/components/ui/RadioRow";
import { TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import type { Agent } from "@/lib/types";
import { useEffect, useState, type ReactNode } from "react";

/** Country restrictions have no backend field yet — rendered read-only. */
export function CountryTags({ countries }: { countries: string[] }) {
  const FLAGS: Record<string, string> = {
    Canada: "🇨🇦",
    "United States": "🇺🇸",
    US: "🇺🇸",
    CA: "🇨🇦",
  };
  return (
    <div
      className="flex items-center gap-2 rounded-lg border border-line bg-app/60 px-2 py-1.5"
      title="Not available yet"
    >
      <div className="flex grow flex-wrap gap-1.5">
        {countries.map((c) => (
          <span
            key={c}
            className="inline-flex items-center gap-1.5 rounded-md bg-app px-2 py-1 text-[12.5px] font-medium text-sub"
          >
            <span>{FLAGS[c] ?? "🌐"}</span>
            {c}
          </span>
        ))}
      </div>
    </div>
  );
}

export default function AgentCard({
  title,
  agents,
  selectedAgentId,
  versionTag,
  onSelectAgent,
  children,
}: {
  title: string;
  agents: Agent[];
  selectedAgentId?: string | null;
  versionTag?: string;
  onSelectAgent: (agentId: string | null) => void;
  children?: ReactNode;
}) {
  const selected = agents.find((a) => a.agent_id === selectedAgentId);

  return (
    <section>
      <h2 className="mb-2 text-[14px] font-semibold">{title}</h2>
      <div className="space-y-4 rounded-xl border border-line bg-white p-4 shadow-sm">
        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[13px] font-medium">Call Agent</span>
            {selected && versionTag && <Badge tone="gray">{versionTag}</Badge>}
          </div>
          <Select
            value={selectedAgentId ?? ""}
            onChange={(v) => onSelectAgent(v || null)}
            className="w-full"
            options={[
              { value: "", label: "No agent" },
              ...agents.map((a) => ({ value: a.agent_id, label: a.agent_name })),
            ]}
          />
        </div>
        {children}
      </div>
    </section>
  );
}

export function WebhookCheckbox({
  url,
  onSave,
}: {
  url?: string;
  onSave: (url: string | null) => void;
}) {
  const [enabled, setEnabled] = useState(Boolean(url));
  const [value, setValue] = useState(url ?? "");

  useEffect(() => {
    setEnabled(Boolean(url));
    setValue(url ?? "");
  }, [url]);

  function save() {
    const next = value.trim();
    if (next && next !== (url ?? "")) onSave(next);
  }

  return (
    <div className="space-y-2">
      <CheckboxRow
        checked={enabled}
        onChange={(checked) => {
          setEnabled(checked);
          if (!checked) {
            setValue("");
            if (url) onSave(null);
          }
        }}
        label={
          <span>
            Add an inbound webhook.{" "}
            <span className="text-sub">(Learn more)</span>
          </span>
        }
      />
      {enabled && (
        <TextInput
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => e.key === "Enter" && save()}
          placeholder="https://your-server.com/inbound"
        />
      )}
    </div>
  );
}
