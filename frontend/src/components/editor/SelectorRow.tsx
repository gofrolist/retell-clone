"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Select from "@/components/ui/Select";
import { voiceNameFromId } from "@/lib/api";
import { LLM_MODELS } from "@/lib/models";
import { withValue } from "@/lib/utils";
import { BookOpen, Clock4, Settings2, Sparkles } from "lucide-react";

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
  const modelOptions = withValue(
    LLM_MODELS.map((m) => ({ value: m.id, label: m.label })),
    model,
  );

  const voiceOptions = withValue(
    voices.map((v) => ({ value: v.voice_id, label: v.voice_name })),
    voiceId,
    voiceNameFromId(voiceId),
  );
  const voiceName = voices.find((v) => v.voice_id === voiceId)?.voice_name ?? voiceNameFromId(voiceId);

  const languageOptions = withValue(LANGUAGES, language);
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
