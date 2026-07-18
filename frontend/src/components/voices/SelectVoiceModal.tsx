"use client";

import { VoiceAvatar } from "@/components/agents/AgentsTable";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import EmptyState from "@/components/ui/EmptyState";
import Modal from "@/components/ui/Modal";
import SearchInput from "@/components/ui/SearchInput";
import Select from "@/components/ui/Select";
import { PILL_ACTIVE_CLASSES, PILL_CONTAINER_CLASSES } from "@/components/ui/Tabs";
import Tooltip from "@/components/ui/Tooltip";
import { voiceNameFromId } from "@/lib/api";
import type { Voice } from "@/lib/types";
import { cn, pressableProps } from "@/lib/utils";
import { AudioLines, Check, Pause, Play, Plus } from "lucide-react";
import { useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { resolvePreviewUrl, useVoicePreview } from "./useVoicePreview";

// Provider tabs. Cartesia ships for the STT→LLM→TTS pipeline; Gemini Live
// ships for the speech-to-speech realtime model. The two are mutually
// exclusive per agent — which one is usable depends on whether a Gemini Live
// model is selected (liveMode). The rest are visible-but-disabled (Retell
// parity). Selecting a tab filters the table to that provider's voices.
const PROVIDERS = [
  { key: "minimax", label: "MiniMax" },
  { key: "fish", label: "Fish Audio" },
  { key: "elevenlabs", label: "ElevenLabs" },
  { key: "cartesia", label: "Cartesia" },
  { key: "openai", label: "OpenAI" },
  { key: "gemini", label: "Gemini Live" },
];

// Which provider tabs are selectable, given the agent's LLM mode.
function providerState(key: string, liveMode: boolean): { enabled: boolean; reason?: string } {
  if (key === "gemini") {
    return liveMode
      ? { enabled: true }
      : { enabled: false, reason: "Select a Gemini Live model to use these voices" };
  }
  if (key === "cartesia") {
    return liveMode
      ? { enabled: false, reason: "Not available with Gemini Live" }
      : { enabled: true };
  }
  return { enabled: false, reason: "Coming soon" };
}

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
  // Gemini voices carry no accent/age; fall back to their one-word descriptor.
  return [v.accent, v.age].filter(Boolean).join(" · ") || v.description || "";
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
  // Mirror the hook's actual playability check, or the button looks live
  // while clicks silently no-op (e.g. a scheme-less ARHITEQ_PUBLIC_API_URL).
  const canPlay = resolvePreviewUrl(voice.preview_audio_url) !== null;
  const playing = playingId === voice.voice_id;
  // No committed sample (e.g. Gemini Live voices): render a same-size spacer
  // so rows stay aligned, rather than a dead greyed-out button.
  if (!canPlay) return <span className="size-7 shrink-0" aria-hidden />;
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onToggle(voice.voice_id, voice.preview_audio_url);
      }}
      aria-label={`${playing ? "Pause" : "Play"} ${voice.voice_name} preview`}
      className="flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-full border border-line bg-white transition-colors hover:bg-app"
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
  liveMode = false,
}: {
  voices: Voice[];
  currentVoiceId: string;
  onSelect: (voiceId: string) => void;
  onClose: () => void;
  // The agent runs a Gemini Live model, so only Gemini native-audio voices
  // apply. Opens on the Gemini tab and disables the Cartesia tab.
  liveMode?: boolean;
}) {
  const [gender, setGender] = useState("all");
  const [accent, setAccent] = useState("all");
  const [age, setAge] = useState("all");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(currentVoiceId);
  const [provider, setProvider] = useState(() => (liveMode ? "gemini" : "cartesia"));
  const { playingId, toggle } = useVoicePreview();

  // An agent imported from Retell can store a voice outside our catalog
  // (e.g. "11labs-…"). Pin it as a row so it stays visible, searchable, and
  // re-selectable — the old native <select> injected it via withValue().
  const allVoices = useMemo<Voice[]>(() => {
    if (!currentVoiceId || voices.some((v) => v.voice_id === currentVoiceId)) return voices;
    return [
      {
        voice_id: currentVoiceId,
        voice_name: voiceNameFromId(currentVoiceId),
        provider: currentVoiceId.split("-")[0].toLowerCase(),
      },
      ...voices,
    ];
  }, [voices, currentVoiceId]);

  const accents = useMemo(() => {
    const distinct = [...new Set(allVoices.map((v) => v.accent).filter(Boolean))] as string[];
    return [{ value: "all", label: "Accent" }, ...distinct.sort().map((a) => ({ value: a, label: a }))];
  }, [allVoices]);

  // Gemini Live voices carry no gender/accent/age, so the trait filters would
  // only ever yield "no matches" — hide and ignore them for that provider.
  const supportsTraitFilters = provider !== "gemini";

  const filtersActive =
    (supportsTraitFilters && (gender !== "all" || accent !== "all" || age !== "all")) ||
    search !== "";

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return allVoices.filter(
      (v) =>
        // Always keep the current voice reselectable, even when it belongs to
        // a provider whose tab is disabled (e.g. an imported "openai-Cimo").
        (v.provider === provider || v.voice_id === currentVoiceId) &&
        (!supportsTraitFilters || gender === "all" || v.gender === gender) &&
        (!supportsTraitFilters || accent === "all" || v.accent === accent) &&
        (!supportsTraitFilters || age === "all" || v.age === age) &&
        (q === "" ||
          v.voice_name.toLowerCase().includes(q) ||
          v.voice_id.toLowerCase().includes(q)),
    );
  }, [allVoices, currentVoiceId, provider, supportsTraitFilters, gender, accent, age, search]);

  const recommended = useMemo(
    () => voices.filter((v) => v.recommended && v.provider === provider),
    [voices, provider],
  );
  const selectedVoice = useMemo(
    () => allVoices.find((v) => v.voice_id === selected),
    [allVoices, selected],
  );

  const applyVoice = (voiceId: string) => {
    // Re-applying the unchanged voice must not dirty the editor draft.
    if (voiceId !== currentVoiceId) onSelect(voiceId);
    onClose();
  };

  // Listbox keyboard model for the voice table: roving tabindex, arrow keys,
  // Home/End, and buffered type-ahead by voice name.
  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);
  const typeahead = useRef({ buf: "", at: 0 });
  const rovingIdx = Math.max(0, filtered.findIndex((v) => v.voice_id === selected));

  const onRowKeyDown = (e: ReactKeyboardEvent<HTMLTableRowElement>, i: number) => {
    // Only when the row itself is focused — let inner controls
    // (e.g. the play/Use Voice buttons) handle their own keys.
    if (e.target !== e.currentTarget) return;
    const focusRow = (j: number) => rowRefs.current[j]?.focus();
    switch (e.key) {
      case "Enter":
      case " ":
        e.preventDefault();
        setSelected(filtered[i].voice_id);
        return;
      case "ArrowDown":
        e.preventDefault();
        focusRow(Math.min(i + 1, filtered.length - 1));
        return;
      case "ArrowUp":
        e.preventDefault();
        focusRow(Math.max(i - 1, 0));
        return;
      case "Home":
        e.preventDefault();
        focusRow(0);
        return;
      case "End":
        e.preventDefault();
        focusRow(filtered.length - 1);
        return;
    }
    if (e.key.length === 1 && !e.metaKey && !e.ctrlKey && !e.altKey) {
      const t = typeahead.current;
      t.buf = e.timeStamp - t.at > 800 ? e.key : t.buf + e.key;
      t.at = e.timeStamp;
      const q = t.buf.toLowerCase();
      const from = t.buf.length === 1 ? i + 1 : i;
      for (let step = 0; step < filtered.length; step++) {
        const j = (from + step) % filtered.length;
        if (filtered[j].voice_name.toLowerCase().startsWith(q)) {
          focusRow(j);
          return;
        }
      }
    }
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
            <Button
              variant="primary"
              disabled={!selected || selected === currentVoiceId}
              onClick={() => applyVoice(selected)}
            >
              Save
            </Button>
          </div>
        </div>
      }
    >
      <div className={cn("grid grid-cols-3 sm:grid-cols-6", PILL_CONTAINER_CLASSES)}>
        {PROVIDERS.map((p) => {
          const { enabled, reason } = providerState(p.key, liveMode);
          if (!enabled) {
            return (
              <Tooltip key={p.key} label={reason ?? "Coming soon"} side="bottom" className="w-full">
                <button
                  disabled
                  className="w-full rounded-md px-3 py-1.5 text-center text-[13px] font-medium text-faint cursor-not-allowed"
                >
                  {p.label}
                </button>
              </Tooltip>
            );
          }
          return (
            <button
              key={p.key}
              onClick={() => setProvider(p.key)}
              className={cn(
                "rounded-md px-3 py-1.5 text-center text-[13px] font-medium transition-colors cursor-pointer",
                provider === p.key ? PILL_ACTIVE_CLASSES : "text-sub hover:text-ink",
              )}
            >
              {p.label}
            </button>
          );
        })}
      </div>

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
        {supportsTraitFilters && (
          <>
            <Select value={gender} onChange={setGender} options={GENDERS} className="w-32" />
            <Select value={accent} onChange={setAccent} options={accents} className="w-32" />
            <Select value={age} onChange={setAge} options={AGES} className="w-36" />
          </>
        )}
        <SearchInput value={search} onChange={setSearch} className="min-w-48 grow" />
      </div>

      {liveMode && selectedVoice && selectedVoice.provider !== "gemini" && (
        <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12.5px] text-amber-800">
          “{selectedVoice.voice_name}” isn’t a Gemini voice. Gemini Live speaks with a
          Gemini native-audio voice — pick one below.
        </div>
      )}

      {!filtersActive && recommended.length > 0 && (
        <div className="mt-4">
          <h3 className="text-[13px] font-semibold">Recommended Voices</h3>
          <div className="mt-2 grid grid-cols-2 gap-3 lg:grid-cols-4">
            {recommended.map((v, i) => (
              <div
                key={v.voice_id}
                {...pressableProps(`Select voice ${v.voice_name}`, () => setSelected(v.voice_id))}
                className={cn(
                  "flex items-center gap-2.5 rounded-xl border p-3 text-left transition-colors cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-accent/30",
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
              </div>
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
            <tbody role="listbox" aria-label="Voices">
              {filtered.map((v, i) => (
                <tr
                  key={v.voice_id}
                  ref={(el) => {
                    rowRefs.current[i] = el;
                  }}
                  onClick={() => setSelected(v.voice_id)}
                  onKeyDown={(e) => onRowKeyDown(e, i)}
                  tabIndex={i === rovingIdx ? 0 : -1}
                  role="option"
                  aria-selected={selected === v.voice_id}
                  aria-label={`Select voice ${v.voice_name}`}
                  className={cn(
                    "group/row cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:ring-inset",
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
                          "invisible group-hover/row:visible group-focus-within/row:visible",
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
