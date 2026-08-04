"use client";

import { formatDuration } from "@/lib/utils";
import { useEffect, useState } from "react";

/**
 * Elapsed time since `start`, re-rendered every second.
 *
 * A call in progress has no duration_ms yet — the worker only reports one at
 * finalize — so anything showing a live call's length has to count locally.
 */
export default function LiveDuration({ start }: { start: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  if (!start) return <span className="text-sub">-</span>;
  return <span className="tabular-nums">{formatDuration(now - start)}</span>;
}
