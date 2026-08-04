"use client";

import Transcript from "./Transcript";
import type { TranscriptItem } from "@/lib/types";
import { ArrowDown } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** Distance from the bottom (px) still counted as "following the newest turn". */
const AT_BOTTOM_SLACK = 48;

/**
 * A transcript that grows during the call: it follows the newest turn on its
 * own, but stops the moment the reader scrolls up to read something earlier —
 * yanking the view back mid-sentence is the fastest way to make a live
 * transcript unusable. A Jump to latest control re-attaches.
 */
export default function LiveTranscript({ turns }: { turns: TranscriptItem[] }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [following, setFollowing] = useState(true);

  // Layout effect, not effect: scroll before paint so a new turn never shows
  // up as a visible jump.
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (box && following) box.scrollTop = box.scrollHeight;
  }, [turns, following]);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const onScroll = () => {
      const distance = box.scrollHeight - box.scrollTop - box.clientHeight;
      setFollowing(distance <= AT_BOTTOM_SLACK);
    };
    box.addEventListener("scroll", onScroll, { passive: true });
    return () => box.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <div className="relative">
      <div ref={boxRef} className="max-h-[52vh] overflow-y-auto pr-1">
        {turns.length === 0 ? (
          <p className="py-8 text-center text-[13px] text-sub">
            Waiting for the first turn of the conversation…
          </p>
        ) : (
          <Transcript turns={turns} />
        )}
      </div>
      {!following && (
        <button
          onClick={() => setFollowing(true)}
          className="absolute bottom-2 left-1/2 -translate-x-1/2 inline-flex items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1 text-[12px] shadow-sm cursor-pointer hover:bg-app"
        >
          <ArrowDown className="size-3.5" />
          Jump to latest
        </button>
      )}
    </div>
  );
}
