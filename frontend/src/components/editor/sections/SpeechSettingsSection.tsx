"use client";

import { Field, TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Slider from "@/components/ui/Slider";
import type { PronunciationEntry } from "@/lib/api";
import { Plus, Settings2, Trash2 } from "lucide-react";
import { useState } from "react";

export default function SpeechSettingsSection({
  ambientSound,
  onAmbientSound,
  ambientSoundVolume,
  onAmbientSoundVolume,
  responsiveness,
  onResponsiveness,
  interruptionSensitivity,
  onInterruptionSensitivity,
  reminderTriggerMs,
  onReminderTriggerMs,
  reminderMaxCount,
  onReminderMaxCount,
  pronunciation,
  onPronunciation,
}: {
  ambientSound: string;
  onAmbientSound: (v: string | null) => void;
  ambientSoundVolume: number;
  onAmbientSoundVolume: (v: number) => void;
  responsiveness: number;
  onResponsiveness: (v: number) => void;
  interruptionSensitivity: number;
  onInterruptionSensitivity: (v: number) => void;
  reminderTriggerMs: number;
  onReminderTriggerMs: (v: number) => void;
  reminderMaxCount: number;
  onReminderMaxCount: (v: number) => void;
  pronunciation: PronunciationEntry[];
  onPronunciation: (v: PronunciationEntry[] | null) => void;
}) {
  const [volumeOpen, setVolumeOpen] = useState(false);
  const noSound = !ambientSound || ambientSound === "none";

  const patchEntry = (i: number, patch: Partial<PronunciationEntry>) => {
    const next = pronunciation.map((row, idx) => (idx === i ? { ...row, ...patch } : row));
    onPronunciation(next.length ? next : null);
  };

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
              { value: "summer-outdoor", label: "Summer Outdoor" },
              { value: "mountain-outdoor", label: "Mountain Outdoor" },
              { value: "call-center", label: "Call Center" },
              { value: "static-noise", label: "Static Noise" },
            ]}
          />
          <div className="relative">
            <button
              onClick={() => setVolumeOpen((v) => !v)}
              disabled={noSound}
              title={noSound ? "Select a background sound first" : "Background sound volume"}
              className="flex size-9 items-center justify-center rounded-lg border border-line bg-white text-sub transition-colors hover:bg-app cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
              aria-label="Background sound settings"
            >
              <Settings2 className="size-4" />
            </button>
            {volumeOpen && (
              <>
                <div className="fixed inset-0 z-20" onClick={() => setVolumeOpen(false)} />
                <div className="absolute right-0 top-full z-30 mt-1 w-56 rounded-xl border border-line bg-white p-3 shadow-lg">
                  <Slider
                    label="Volume"
                    min={0}
                    max={2}
                    step={0.1}
                    value={ambientSoundVolume}
                    onChange={onAmbientSoundVolume}
                  />
                </div>
              </>
            )}
          </div>
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

      <Field
        label="Pronunciation"
        hint="Guide how the voice pronounces specific words (IPA or CMU phonemes)."
      >
        <div className="space-y-1.5">
          {pronunciation.map((p, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <TextInput
                value={p.word}
                onChange={(e) => patchEntry(i, { word: e.target.value })}
                placeholder="Word"
              />
              <Select
                value={p.alphabet}
                onChange={(v) => patchEntry(i, { alphabet: v as "ipa" | "cmu" })}
                options={[
                  { value: "ipa", label: "IPA" },
                  { value: "cmu", label: "CMU" },
                ]}
              />
              <TextInput
                value={p.phoneme}
                onChange={(e) => patchEntry(i, { phoneme: e.target.value })}
                placeholder="Phoneme"
              />
              <button
                onClick={() => {
                  const next = pronunciation.filter((_, idx) => idx !== i);
                  onPronunciation(next.length ? next : null);
                }}
                className="rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
                aria-label="Delete pronunciation row"
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
          <button
            onClick={() =>
              onPronunciation([...pronunciation, { word: "", alphabet: "ipa", phoneme: "" }])
            }
            className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
          >
            <Plus className="size-3.5" /> Add
          </button>
        </div>
      </Field>
    </div>
  );
}
