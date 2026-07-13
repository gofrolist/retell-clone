"use client";

import { formatDuration, isHttpUrl } from "@/lib/utils";
import { Download, Play, Pause } from "lucide-react";
import { useRef, useState } from "react";

/** Audio player for call recordings, matching the call drawer design. */
export default function AudioPlayer({
  src,
  durationMs = 0,
}: {
  src: string;
  durationMs?: number;
}) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const barRef = useRef<HTMLDivElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [currentSec, setCurrentSec] = useState(0);
  const [durationSec, setDurationSec] = useState(durationMs / 1000);

  const progress = durationSec > 0 ? Math.min(1, currentSec / durationSec) : 0;
  // Only trust http(s) recording URLs — never render javascript:/data: schemes.
  const safeSrc = src && isHttpUrl(src) ? src : undefined;

  function toggle() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) void audio.play();
    else audio.pause();
  }

  function seek(e: React.MouseEvent<HTMLDivElement>) {
    const audio = audioRef.current;
    const bar = barRef.current;
    if (!audio || !bar || !durationSec) return;
    const rect = bar.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * durationSec;
    setCurrentSec(ratio * durationSec);
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
