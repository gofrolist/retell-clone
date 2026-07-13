"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import Modal from "@/components/ui/Modal";
import SearchInput from "@/components/ui/SearchInput";
import Select from "@/components/ui/Select";
import { UnderlineTabs } from "@/components/ui/Tabs";
import Tooltip from "@/components/ui/Tooltip";
import { voiceNameFromId } from "@/lib/api";
import type { Voice } from "@/lib/types";
import { cn } from "@/lib/utils";
import { AudioLines, Check, Pause, Play, Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { useVoicePreview } from "./useVoicePreview";

const TOP_TABS = [
  { key: "platform", label: "Platform Voices" },
  { key: "custom", label: "Custom Providers" },
];

// Only Cartesia ships today; the rest are visible but disabled (Retell parity).
const PROVIDERS = [
  { key: "minimax", label: "MiniMax", enabled: false },
  { key: "fish", label: "Fish Audio", enabled: false },
  { key: "elevenlabs", label: "ElevenLabs", enabled: false },
  { key: "cartesia", label: "Cartesia", enabled: true },
  { key: "openai", label: "OpenAI", enabled: false },
];

const GENDERS = [
  { value: "all", label: "Gender" },
  { value: "female", label: "Female" },
  { value: "male", label: "Male" },
];

const AGES = [
  { value: "all", label: "Age" },
  { value: "Young", label: "Young" },
  { value: "Middle Aged", label: "Middle Aged" },
  { value: "Old", label: "Old" },
];

function traitLine(v: Voice): string {
  return [v.accent, v.age].filter(Boolean).join(" · ");
}

function PlayButton({
  voice,
  playingId,
  onToggle,
}: {
  voice: Voice;
  playingId: string | null;
  onToggle: (voiceId: string, previewUrl: string | null | undefined) => void;
}) {
  const canPlay = Boolean(voice.preview_audio_url);
  const playing = playingId === voice.voice_id;
  return (
    <button
      disabled={!canPlay}
      title={canPlay ? undefined : "Preview not available yet"}
      onClick={(e) => {
        e.stopPropagation();
        onToggle(voice.voice_id, voice.preview_audio_url);
      }}
      aria-label={`${playing ? "Pause" : "Play"} ${voice.voice_name} preview`}
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-full border border-line bg-white transition-colors",
        canPlay ? "cursor-pointer hover:bg-app" : "opacity-40 cursor-not-allowed",
      )}
    >
      {playing ? (
        <Pause className="size-3.5" />
      ) : (
        <Play className="size-3.5 translate-x-px" />
      )}
    </button>
  );
}

export default function SelectVoiceModal({
  voices,
  currentVoiceId,
  onSelect,
  onClose,
}: {
  voices: Voice[];
  currentVoiceId: string;
  onSelect: (voiceId: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState("platform");
  const [gender, setGender] = useState("all");
  const [accent, setAccent] = useState("all");
  const [age, setAge] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(currentVoiceId);
  const { playingId, toggle } = useVoicePreview();

  const accents = useMemo(() => {
    const distinct = [...new Set(voices.map((v) => v.accent).filter(Boolean))] as string[];
    return [{ value: "all", label: "Accent" }, ...distinct.sort().map((a) => ({ value: a, label: a }))];
  }, [voices]);

  const filtersActive = gender !== "all" || accent !== "all" || age !== "all" || search !== "";

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return voices.filter(
      (v) =>
        (gender === "all" || v.gender === gender) &&
        (accent === "all" || v.accent === accent) &&
        (age === "all" || v.age === age) &&
        (q === "" ||
          v.voice_name.toLowerCase().includes(q) ||
          v.voice_id.toLowerCase().includes(q)),
    );
  }, [voices, gender, accent, age, search]);

  const recommended = voices.filter((v) => v.recommended);
  const selectedVoice = voices.find((v) => v.voice_id === selected);

  const applyVoice = (voiceId: string) => {
    onSelect(voiceId);
    onClose();
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Select Voice"
      width="max-w-5xl"
      footer={
        <div className="flex w-full items-center justify-between">
          <div className="flex items-center gap-2.5">
            {selected ? (
              <>
                <VoiceAvatar
                  name={selectedVoice?.voice_name ?? voiceNameFromId(selected)}
                  index={0}
                />
                <div>
                  <div className="text-[13px] font-medium leading-tight">
                    {selectedVoice?.voice_name ?? voiceNameFromId(selected)}
                  </div>
                  {selectedVoice && (
                    <div className="text-xs text-sub leading-tight">
                      {traitLine(selectedVoice)}
                    </div>
                  )}
                </div>
              </>
            ) : (
              <span className="text-[13px] text-sub">No voice selected</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!selected} onClick={() => applyVoice(selected)}>
              Save
            </Button>
          </div>
        </div>
      }
    >
      <UnderlineTabs tabs={TOP_TABS} active={tab} onChange={setTab} />

      {tab === "custom" && (
        <div className="mt-4 grid grid-cols-5 gap-0.5 rounded-lg border border-line bg-app p-0.5">
          {PROVIDERS.map((p) =>
            p.enabled ? (
              <button
                key={p.key}
                className="rounded-md border border-line bg-white px-3 py-1.5 text-center text-[13px] font-medium text-ink shadow-sm"
              >
                {p.label}
              </button>
            ) : (
              <Tooltip key={p.key} label="Coming soon" className="w-full">
                <button
                  disabled
                  className="w-full rounded-md px-3 py-1.5 text-center text-[13px] font-medium text-faint cursor-not-allowed"
                >
                  {p.label}
                </button>
              </Tooltip>
            ),
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Tooltip label="Coming soon">
          <button
            disabled
            className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-ink px-3 text-[13px] font-medium text-white opacity-40 cursor-not-allowed"
          >
            <Plus className="size-4" />
            Add custom voice
          </button>
        </Tooltip>
        <Select value={gender} onChange={setGender} options={GENDERS} className="w-32" />
        <Select value={accent} onChange={setAccent} options={accents} className="w-32" />
        <Select value={age} onChange={setAge} options={AGES} className="w-36" />
        <SearchInput value={search} onChange={setSearch} className="min-w-48 grow" />
      </div>

      {!filtersActive && recommended.length > 0 && (
        <div className="mt-4">
          <h3 className="text-[13px] font-semibold">Recommended Voices</h3>
          <div className="mt-2 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {recommended.map((v, i) => (
              <button
                key={v.voice_id}
                onClick={() => setSelected(v.voice_id)}
                className={cn(
                  "flex items-center gap-2.5 rounded-xl border p-3 text-left transition-colors cursor-pointer",
                  selected === v.voice_id
                    ? "border-accent ring-2 ring-accent/15"
                    : "border-line hover:bg-app",
                )}
              >
                <VoiceAvatar name={v.voice_name} index={i} />
                <span className="min-w-0 grow">
                  <span className="block truncate text-[13px] font-medium leading-tight">
                    {v.voice_name}
                  </span>
                  <span className="block truncate text-xs text-sub leading-tight">
                    {traitLine(v)}
                  </span>
                  <span className="block truncate text-xs text-faint leading-tight">
                    ID: {v.voice_id}
                  </span>
                </span>
                <PlayButton voice={v} playingId={playingId} onToggle={toggle} />
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4">
        {filtered.length === 0 ? (
          <EmptyState
            icon={AudioLines}
            title="No voices match"
            description="Try clearing the filters or searching for a different name."
          />
        ) : (
          <table className="w-full border-separate border-spacing-0 text-[13px]">
            <thead>
              <tr className="text-left text-xs text-sub">
                <th className="rounded-l-lg border-y border-l border-line bg-app px-3 py-2 font-medium">
                  Voice
                </th>
                <th className="border-y border-line bg-app px-3 py-2 font-medium">Trait</th>
                <th className="rounded-r-lg border-y border-r border-line bg-app px-3 py-2 font-medium">
                  Voice ID
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((v, i) => (
                <tr
                  key={v.voice_id}
                  onClick={() => setSelected(v.voice_id)}
                  className={cn(
                    "group/row cursor-pointer",
                    selected === v.voice_id ? "bg-accent/5" : "hover:bg-app",
                  )}
                >
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center gap-2.5">
                      <PlayButton voice={v} playingId={playingId} onToggle={toggle} />
                      <VoiceAvatar name={v.voice_name} index={i} />
                      <span className="font-medium">{v.voice_name}</span>
                    </span>
                  </td>
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center gap-1.5">
                      {v.accent && <Badge tone="gray">{v.accent}</Badge>}
                      {v.age && <Badge tone="gray">{v.age}</Badge>}
                    </span>
                  </td>
                  <td className="border-b border-line px-3 py-2.5">
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-mono text-xs text-sub">{v.voice_id}</span>
                      <Button
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          applyVoice(v.voice_id);
                        }}
                        className={cn(
                          "invisible group-hover/row:visible",
                          selected === v.voice_id && "visible",
                        )}
                      >
                        <Check className="size-3.5" />
                        Use Voice
                      </Button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Modal>
  );
}
