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

const E164 = /^\+[1-9]\d{6,14}$/;

/** True if `v` is a valid E.164 phone number (e.g. "+14155550123"). */
export function isE164(v: string): boolean {
  return E164.test(v);
}
