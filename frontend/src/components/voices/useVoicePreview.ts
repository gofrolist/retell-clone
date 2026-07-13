"use client";

import { API_BASE } from "@/lib/api";
import { useEffect, useRef, useState } from "react";

/**
 * Same guard as AudioPlayer's safeSrc: only http(s) plays. The API returns a
 * relative /static/... path when ARCHITEQ_PUBLIC_API_URL is unset (local
 * dev); resolve it against the API origin, not the dashboard origin.
 */
export function resolvePreviewUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  if (/^https?:/i.test(url)) return url;
  if (url.startsWith("/static/")) return `${API_BASE}${url}`;
  return null;
}

/** One shared Audio element: starting a preview stops the previous one. */
export function useVoicePreview() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      audioRef.current = null;
    },
    [],
  );

  const toggle = (voiceId: string, previewUrl: string | null | undefined) => {
    if (playingId === voiceId) {
      audioRef.current?.pause();
      audioRef.current = null;
      setPlayingId(null);
      return;
    }
    const src = resolvePreviewUrl(previewUrl);
    if (!src) return;
    audioRef.current?.pause();
    const audio = new Audio(src);
    audioRef.current = audio;
    const reset = () => {
      // A replaced preview's events must not clobber the current one.
      if (audioRef.current !== audio) return;
      audioRef.current = null;
      setPlayingId(null);
    };
    audio.onended = reset;
    audio.onerror = reset;
    setPlayingId(voiceId);
    audio.play().catch(reset);
  };

  return { playingId, toggle };
}
