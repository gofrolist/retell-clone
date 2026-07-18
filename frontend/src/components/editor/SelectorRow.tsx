"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import LlmModelSelect from "@/components/editor/LlmModelSelect";
import Select from "@/components/ui/Select";
import SelectVoiceModal from "@/components/voices/SelectVoiceModal";
import { voiceNameFromId } from "@/lib/api";
import { isLiveModel } from "@/lib/models";
import type { Voice } from "@/lib/types";
import { withValue } from "@/lib/utils";
import { BookOpen, ChevronDown, Clock4, Settings2 } from "lucide-react";
import { useState } from "react";

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
  voices: Voice[];
}) {
  const [voiceModalOpen, setVoiceModalOpen] = useState(false);
  const live = isLiveModel(model);

  const voiceName = voices.find((v) => v.voice_id === voiceId)?.voice_name ?? voiceNameFromId(voiceId);

  const languageOptions = withValue(LANGUAGES, language);
  const flag = LANGUAGES.find((l) => l.value === language)?.flag ?? "🌐";

  return (
    <div className="flex flex-wrap items-center gap-2">
      {onModel && (
        <>
          <LlmModelSelect value={model} onChange={onModel} />
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
      <button
        onClick={() => setVoiceModalOpen(true)}
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white pl-2 pr-2.5 text-[13px] font-medium transition-colors hover:bg-app cursor-pointer"
        aria-haspopup="dialog"
      >
        <VoiceAvatar name={voiceName} index={0} />
        {voiceName}
        <ChevronDown className="size-3.5 text-faint" />
      </button>
      {voiceModalOpen && (
        <SelectVoiceModal
          voices={voices}
          currentVoiceId={voiceId}
          onSelect={onVoice}
          onClose={() => setVoiceModalOpen(false)}
          liveMode={live}
        />
      )}
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
