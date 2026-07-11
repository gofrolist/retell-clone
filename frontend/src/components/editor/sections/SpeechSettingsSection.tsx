"use client";

import { Field } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Slider from "@/components/ui/Slider";
import { Plus, Settings2 } from "lucide-react";

export default function SpeechSettingsSection({
  ambientSound,
  onAmbientSound,
  responsiveness,
  onResponsiveness,
  interruptionSensitivity,
  onInterruptionSensitivity,
  reminderTriggerMs,
  onReminderTriggerMs,
  reminderMaxCount,
  onReminderMaxCount,
}: {
  ambientSound: string;
  onAmbientSound: (v: string | null) => void;
  responsiveness: number;
  onResponsiveness: (v: number) => void;
  interruptionSensitivity: number;
  onInterruptionSensitivity: (v: number) => void;
  reminderTriggerMs: number;
  onReminderTriggerMs: (v: number) => void;
  reminderMaxCount: number;
  onReminderMaxCount: (v: number) => void;
}) {
  return (
    <div className="space-y-5">
      <Field label="Background Sound">
        <div className="flex items-center gap-2">
          <Select
            value={ambientSound || "none"}
            onChange={(v) => onAmbientSound(v === "none" ? null : v)}
            className="grow"
            options={[
              { value: "none", label: "None" },
              { value: "coffee-shop", label: "Coffee Shop" },
              { value: "convention-hall", label: "Convention Hall" },
              { value: "call-center", label: "Call Center" },
              { value: "static-noise", label: "Static Noise" },
            ]}
          />
          <button
            disabled
            title="Not available yet"
            className="flex size-9 items-center justify-center rounded-lg border border-line bg-white text-sub opacity-40 cursor-not-allowed"
            aria-label="Background sound settings"
          >
            <Settings2 className="size-4" />
          </button>
        </div>
      </Field>

      <Slider
        label="Response Eagerness"
        min={0}
        max={1}
        step={0.01}
        value={responsiveness}
        onChange={onResponsiveness}
        leftHint="patient"
        rightHint="eager"
      />

      <Slider
        label="Interruption Sensitivity"
        min={0}
        max={1}
        step={0.01}
        value={interruptionSensitivity}
        onChange={onInterruptionSensitivity}
      />

      <Field label="Reminder Message Frequency" hint="If the user is silent, send a reminder.">
        <div className="flex items-center gap-2 text-[13px] text-sub">
          <input
            value={reminderTriggerMs / 1000}
            onChange={(e) => {
              const s = Number(e.target.value);
              if (Number.isFinite(s) && s >= 0) onReminderTriggerMs(Math.round(s * 1000));
            }}
            inputMode="numeric"
            className="h-9 w-16 rounded-lg border border-line bg-white px-2.5 text-center outline-none focus:border-accent"
          />
          <span>s</span>
          <input
            value={reminderMaxCount}
            onChange={(e) => {
              const n = Number(e.target.value);
              if (Number.isInteger(n) && n >= 0) onReminderMaxCount(n);
            }}
            inputMode="numeric"
            className="h-9 w-16 rounded-lg border border-line bg-white px-2.5 text-center outline-none focus:border-accent"
          />
          <span>times</span>
        </div>
      </Field>

      <Field label="Pronunciation">
        <button
          disabled
          title="Not available yet"
          className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep opacity-40 cursor-not-allowed"
        >
          <Plus className="size-3.5" /> Add
        </button>
      </Field>
    </div>
  );
}
