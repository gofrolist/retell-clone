"use client";

import Select from "@/components/ui/Select";
import { TextInput } from "@/components/ui/Field";
import { Clock } from "lucide-react";

export default function WelcomeMessage({
  startSpeaker,
  onStartSpeaker,
  message,
  onMessage,
  pause,
}: {
  startSpeaker: "agent" | "user";
  onStartSpeaker: (v: "agent" | "user") => void;
  message: string;
  onMessage: (v: string) => void;
  pause: number;
}) {
  return (
    <section className="mt-5">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-[14px] font-semibold">Welcome Message</h2>
        <span className="inline-flex items-center gap-1 text-[13px] text-sub">
          <Clock className="size-3.5" />
          Pause Before Speaking: {pause}s
        </span>
      </div>
      <div className="space-y-2">
        <Select
          value={startSpeaker}
          onChange={(v) => onStartSpeaker(v === "user" ? "user" : "agent")}
          className="w-full"
          options={[
            { value: "agent", label: "AI speaks first" },
            { value: "user", label: "User speaks first" },
          ]}
        />
        <TextInput
          value={message}
          onChange={(e) => onMessage(e.target.value)}
          placeholder={
            startSpeaker === "agent"
              ? "e.g. Hi, how can I help you today?"
              : "Optional message once the user speaks"
          }
        />
      </div>
    </section>
  );
}
