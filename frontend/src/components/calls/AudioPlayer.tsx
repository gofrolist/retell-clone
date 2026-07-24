"use client";

import HoverCard from "@/components/ui/HoverCard";
import { formatDuration, isHttpUrl } from "@/lib/utils";
import { Download, Play, Pause } from "lucide-react";
import { useRef, useState } from "react";

/** Timeline annotation (tool call / KB retrieval) shown as a dot on the bar. */
export interface AudioMarker {
  time_ms: number;
  kind: "tool" | "kb";
  title: string;
  body?: string;
}

/** Audio player for call recordings, matching the call drawer design. */
export default function AudioPlayer({
  src,
  durationMs = 0,
  markers,
}: {
  src: string;
  durationMs?: number;
  markers?: AudioMarker[];
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSec, setCurrentSec] = useState(0);
  const [durationSec, setDurationSec] = useState(durationMs / 1000);

  const progress = durationSec > 0 ? Math.min(1, currentSec / durationSec) : 0;
  // Only trust http(s) recording URLs — never render javascript:/data: schemes.
  const safeSrc = src && isHttpUrl(src) ? src : undefined;
  // Items timed past the recording end would render off-bar — drop them.
  const visibleMarkers =
    durationSec > 0
      ? (markers ?? []).filter((m) => m.time_ms >= 0 && m.time_ms <= durationSec * 1000)
      : [];

  function toggle() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  }

  function seekTo(sec: number) {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = sec;
    setCurrentSec(sec);
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const bar = barRef.current;
    if (!bar || !durationSec) return;
    const rect = bar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    seekTo(ratio * durationSec);
  }

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-white px-3 py-2.5 shadow-sm">
      <audio
        ref={audioRef}
        src={safeSrc}
        preload="metadata"
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onTimeUpdate={(e) => setCurrentSec(e.currentTarget.currentTime)}
        onLoadedMetadata={(e) => {
          if (Number.isFinite(e.currentTarget.duration)) {
            setDurationSec(e.currentTarget.duration);
          }
        }}
      />
      <button
        onClick={toggle}
        className="flex size-8 items-center justify-center rounded-full bg-ink text-white hover:bg-black/80 cursor-pointer shrink-0"
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? <Pause className="size-3.5" /> : <Play className="ml-0.5 size-3.5" />}
      </button>
      <span className="text-xs tabular-nums text-sub shrink-0">
        {formatDuration(currentSec * 1000)}
      </span>
      <div
        ref={barRef}
        onClick={seek}
        className="relative h-1 grow cursor-pointer rounded-full bg-line"
      >
        <div
          className="absolute inset-y-0 left-0 rounded-full bg-ink"
          style={{ width: `${progress * 100}%` }}
        />
        <div
          className="absolute top-1/2 size-3 -translate-y-1/2 rounded-full border border-line-strong bg-white shadow"
          style={{ left: `calc(${progress * 100}% - 6px)` }}
        />
        {visibleMarkers.map((m, i) => (
          <span
            key={i}
            className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${(m.time_ms / (durationSec * 1000)) * 100}%` }}
          >
            <HoverCard
              trigger={
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    seekTo(m.time_ms / 1000);
                  }}
                  aria-label={`Seek to ${formatDuration(m.time_ms)}: ${m.title}`}
                  className={`block size-2.5 rounded-full border-2 border-white shadow cursor-pointer ${
                    m.kind === "tool" ? "bg-accent-deep" : "bg-sub"
                  }`}
                />
              }
            >
              <div className="text-[12px]">
                <div className="mb-0.5 flex items-center justify-between gap-2">
                  <span className="font-medium">{m.title}</span>
                  <span className="tabular-nums text-faint">{formatDuration(m.time_ms)}</span>
                </div>
                {m.body && (
                  <pre className="max-h-40 overflow-hidden font-mono text-[11px] whitespace-pre-wrap break-words text-sub">
                    {m.body}
                  </pre>
                )}
              </div>
            </HoverCard>
          </span>
        ))}
      </div>
      <span className="text-xs tabular-nums text-sub shrink-0">
        {formatDuration(durationSec * 1000)}
      </span>
      {safeSrc && (
        <a
          href={safeSrc}
          download
          target="_blank"
          rel="noopener noreferrer"
          className="rounded-md p-1.5 text-faint hover:bg-app hover:text-ink cursor-pointer shrink-0"
          aria-label="Download recording"
        >
          <Download className="size-4" />
        </a>
      )}
    </div>
  );
}
