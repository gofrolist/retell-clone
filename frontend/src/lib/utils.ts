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
