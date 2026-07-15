import type { KeyboardEvent as ReactKeyboardEvent } from "react";

export function cn(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function formatDuration(ms: number): string {
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function formatDurationLong(ms: number): string {
  const s = Math.round(ms / 1000);
  const m = Math.floor(s / 60);
  return m > 0 ? `${m}m ${s % 60}s` : `${s}s`;
}

export function formatCallTime(ts: number): string {
  // 0 stands in for "never happened" (e.g. start of a call that never
  // connected) — render a placeholder instead of the Unix epoch.
  if (!ts) return "—";
  const d = new Date(ts);
  return (
    d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) +
    " · " +
    d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" })
  );
}

export function formatDate(ts: number): string {
  return new Date(ts).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatCost(cost: number): string {
  return `$${cost.toFixed(3)}`;
}

export function truncateId(id: string, head = 12): string {
  return id.length > head + 2 ? `${id.slice(0, head)}…` : id;
}

/** Display size in whole KB with a 1 KB floor. */
export function kbFromBytes(bytes: number): number {
  return Math.max(1, Math.round(bytes / 1024));
}

/** Client-side "save blob as file" via a temporary anchor. */
export function triggerBlobDownload(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(href);
}

/**
 * Ensure a select can render a stored value that isn't in the preset list by
 * prepending it (shown as `label`, defaulting to the raw value). Empty values
 * return the preset list untouched so no blank option is rendered.
 */
export function withValue(
  options: { value: string; label: string }[],
  value: string,
  label: string = value,
): { value: string; label: string }[] {
  if (!value || options.some((o) => o.value === value)) return options;
  return [{ value, label }, ...options];
}

const E164 = /^\+[1-9]\d{6,14}$/;

/** True if `v` is a valid E.164 phone number (e.g. "+14155550123"). */
export function isE164(v: string): boolean {
  return E164.test(v);
}

/**
 * True for http(s) URLs — the only schemes we ever hand to media elements
 * (never javascript:/data:). Single source for that security check.
 */
export function isHttpUrl(v: string): boolean {
  return /^https?:/i.test(v);
}

/**
 * Spread onto a non-button element (row, card) to make it click- and
 * keyboard-activatable like a button, without stealing keys from nested
 * controls (copy/play buttons handle their own).
 */
export function pressableProps(label: string, onActivate: () => void) {
  return {
    role: "button" as const,
    tabIndex: 0,
    "aria-label": label,
    onClick: onActivate,
    onKeyDown: (e: ReactKeyboardEvent<HTMLElement>) => {
      // Only when the element itself is focused — let inner controls
      // handle their own keys.
      if (e.target !== e.currentTarget) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onActivate();
      }
    },
  };
}
