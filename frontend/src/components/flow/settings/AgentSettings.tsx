"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import CurrentTimeAwareness from "@/components/editor/CurrentTimeAwareness";
import { LANGUAGES } from "@/components/editor/SelectorRow";
import { Field } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import SelectVoiceModal from "@/components/voices/SelectVoiceModal";
import { voiceNameFromId } from "@/lib/api";
import { isLiveModel } from "@/lib/models";
import type { Voice } from "@/lib/types";
import { withValue } from "@/lib/utils";
import { BookOpen, ChevronDown } from "lucide-react";
import { useState } from "react";

/**
 * Agent-level settings for a conversation-flow agent: voice, language and
 * time awareness. These live on the AGENT, not on the flow — which is why
 * they are a slot the page fills (`FlowEditor`'s `globalHeader`) rather than
 * part of `GlobalSettings`, whose every field patches the flow document.
 *
 * They sit here rather than in a bar above the canvas because a flow agent's
 * canvas wants the whole width, and because everything else that applies to
 * the call as a whole is already in this pane.
 */
export default function AgentSettings({
  voiceId,
  onVoice,
  language,
  onLanguage,
  timezone,
  onTimezone,
  voices,
  model,
}: {
  // Voice and timezone are voice-agent settings: a chat agent omits the
  // handlers and the two controls disappear with them, exactly as in
  // `SelectorRow`. Language is NOT voice-only — a chat agent has one too — so
  // it is always rendered.
  voiceId?: string;
  onVoice?: (voiceId: string) => void;
  language: string;
  onLanguage: (language: string) => void;
  /** IANA zone for the agent's time variables; "" = no timezone set. */
  timezone?: string;
  onTimezone?: (timezone: string) => void;
  voices: Voice[];
  /** The flow's model, which decides which voices are selectable. */
  model: string;
}) {
  const [voiceModalOpen, setVoiceModalOpen] = useState(false);
  const live = isLiveModel(model);
  const voiceName = voiceId
    ? (voices.find((v) => v.voice_id === voiceId)?.voice_name ?? voiceNameFromId(voiceId))
    : "";
  const flag = LANGUAGES.find((l) => l.value === language)?.flag ?? "🌐";

  return (
    <div className="space-y-4 border-b border-line p-4">
      <p className="text-[13px] font-medium text-ink">Agent settings</p>

      <div className="flex gap-2">
        {onVoice && (
          <Field label="Voice" className="min-w-0 flex-1">
            <button
              type="button"
              onClick={() => setVoiceModalOpen(true)}
              className="inline-flex h-9 w-full items-center gap-2 rounded-lg border border-line bg-white pr-2 pl-2 text-[13px] font-medium transition-colors hover:bg-app cursor-pointer"
              aria-haspopup="dialog"
            >
              <VoiceAvatar name={voiceName} index={0} />
              <span className="grow truncate text-left">{voiceName || "Select a voice"}</span>
              <ChevronDown className="size-3.5 shrink-0 text-faint" />
            </button>
            {voiceModalOpen && (
              <SelectVoiceModal
                voices={voices}
                currentVoiceId={voiceId ?? ""}
                onSelect={onVoice}
                onClose={() => setVoiceModalOpen(false)}
                liveMode={live}
              />
            )}
          </Field>
        )}

        <Field label="Language" className="min-w-0 flex-1">
          <Select
            value={language}
            onChange={onLanguage}
            className="w-full"
            prefix={<span className="text-sm leading-none">{flag}</span>}
            options={withValue(LANGUAGES, language)}
          />
        </Field>
      </div>

      <div className="flex items-center gap-2">
        {onTimezone && <CurrentTimeAwareness timezone={timezone ?? ""} onTimezone={onTimezone} />}
        <button
          disabled
          title="Not available yet"
          className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-white px-3 text-[13px] font-medium opacity-40 cursor-not-allowed"
        >
          <BookOpen className="size-4 text-sub" />
          Agent Handbook
        </button>
      </div>
    </div>
  );
}
