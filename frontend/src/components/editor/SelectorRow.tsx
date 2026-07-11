"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Select from "@/components/ui/Select";
import { voiceNameFromId } from "@/lib/api";
import { BookOpen, Clock4, Settings2, Sparkles } from "lucide-react";

// Retell-compatible engine model ids the backend stores as-is (the worker
// maps non-Gemini names to its default Gemini model).
const MODEL_LABELS: Record<string, string> = {
  "gemini-2.5-flash": "Gemini 2.5 Flash",
  "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
  "gpt-4o": "GPT-4o",
  "gpt-4o-mini": "GPT-4o mini",
  "claude-3.7-sonnet": "Claude 3.7 Sonnet",
};

const LANGUAGES: { value: string; label: string; flag: string }[] = [
  { value: "en-US", label: "English (US)", flag: "🇺🇸" },
  { value: "en-GB", label: "English (UK)", flag: "🇬🇧" },
  { value: "es-ES", label: "Spanish", flag: "🇪🇸" },
  { value: "fr-FR", label: "French", flag: "🇫🇷" },
  { value: "de-DE", label: "German", flag: "🇩🇪" },
];

export default function SelectorRow({
  model,
  onModel,
  voiceId,
  onVoice,
  language,
  onLanguage,
  voices,
}: {
  model: string;
  onModel?: (v: string) => void;
  voiceId: string;
  onVoice: (v: string) => void;
  language: string;
  onLanguage: (v: string) => void;
  voices: { voice_id: string; voice_name: string }[];
}) {
  const modelIds = Object.keys(MODEL_LABELS);
  if (model && !modelIds.includes(model)) modelIds.unshift(model);
  const modelOptions = modelIds.map((m) => ({ value: m, label: MODEL_LABELS[m] ?? m }));

  const voiceOptions = (
    voices.some((v) => v.voice_id === voiceId)
      ? voices
      : [{ voice_id: voiceId, voice_name: voiceNameFromId(voiceId) }, ...voices]
  ).map((v) => ({ value: v.voice_id, label: v.voice_name }));
  const voiceName = voices.find((v) => v.voice_id === voiceId)?.voice_name ?? voiceNameFromId(voiceId);

  const languageOptions: { value: string; label: string }[] = LANGUAGES.some(
    (l) => l.value === language,
  )
    ? LANGUAGES
    : [{ value: language, label: language }, ...LANGUAGES];
  const flag = LANGUAGES.find((l) => l.value === language)?.flag ?? "🌐";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {onModel && (
        <>
          <Select
            value={model}
            onChange={onModel}
            prefix={<Sparkles className="size-3.5 text-accent" />}
            options={modelOptions}
          />
          <button
            disabled
            title="Not available yet"
            className="flex size-9 items-center justify-center rounded-lg border border-line bg-white text-sub opacity-40 cursor-not-allowed"
            aria-label="Model settings"
          >
            <Settings2 className="size-4" />
          </button>
        </>
      )}
      <Select
        value={voiceId}
        onChange={onVoice}
        prefix={<VoiceAvatar name={voiceName} index={0} />}
        className="[&_select]:pl-10"
        options={voiceOptions}
      />
      <Select
        value={language}
        onChange={onLanguage}
        prefix={<span className="text-sm leading-none">{flag}</span>}
        options={languageOptions}
      />
      <div className="ml-auto flex items-center gap-2">
        <button
          disabled
          title="Not available yet"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-3 text-[13px] font-medium opacity-40 cursor-not-allowed"
        >
          <BookOpen className="size-4 text-sub" />
          Agent Handbook
        </button>
        <button
          disabled
          title="Version history not available yet"
          className="flex size-9 items-center justify-center rounded-lg border border-line bg-white text-sub opacity-40 cursor-not-allowed"
          aria-label="Version history"
        >
          <Clock4 className="size-4" />
        </button>
      </div>
    </div>
  );
}
