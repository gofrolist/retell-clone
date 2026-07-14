"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Clipboard copy with a transient "copied" flag for button labels.
 *
 * `copy(text, key)` marks `key` copied for `resetMs`; check `copiedKey` to
 * render the swap. Pass distinct keys when one component has several copy
 * buttons (a fresh copy retargets the flag instead of racing timers).
 */
export function useCopied(resetMs = 2000) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(
    (text: string, key = "default") => {
      navigator.clipboard?.writeText(text).catch(() => {});
      setCopiedKey(key);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopiedKey(null), resetMs);
    },
    [resetMs],
  );

  return { copiedKey, copy };
}
