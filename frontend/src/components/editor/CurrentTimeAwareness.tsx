"use client";

import Select from "@/components/ui/Select";
import { useClickOutside } from "@/lib/useClickOutside";
import { cn } from "@/lib/utils";
import { Clock4 } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

/** Zone the backend falls back to when the agent has none (Retell's default). */
const FALLBACK_ZONE = "America/Los_Angeles";

/** Every IANA zone the runtime knows, or a short list on older engines. */
function zoneNames(): string[] {
  const supported = (
    Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
  ).supportedValuesOf;
  const zones = supported ? supported("timeZone") : [];
  if (zones.length) return zones;
  return [
    "America/Los_Angeles",
    "America/Denver",
    "America/Chicago",
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Asia/Dubai",
    "Asia/Tokyo",
    "Australia/Sydney",
    "UTC",
  ];
}

/** "GMT-07:00" for a zone right now, or "" if the runtime rejects it. */
function offsetLabel(zone: string): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      timeZoneName: "shortOffset",
    }).formatToParts(new Date());
    return parts.find((p) => p.type === "timeZoneName")?.value ?? "";
  } catch {
    return "";
  }
}

function currentTimeIn(zone: string): string {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      weekday: "long",
      month: "long",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    }).format(new Date());
  } catch {
    return "";
  }
}

/**
 * Retell's "Current Time Awareness" popover behind the clock button: the IANA
 * timezone the agent's un-suffixed time variables ({{current_time}},
 * {{current_hour}}, {{current_calendar}}) resolve in. Empty = no timezone set,
 * which leaves Retell's America/Los_Angeles default in place.
 *
 * Picking a zone edits the page draft; the header Save persists it, same as
 * every other agent field.
 */
export default function CurrentTimeAwareness({
  timezone,
  onTimezone,
}: {
  timezone: string;
  onTimezone: (v: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useClickOutside(
    rootRef,
    useCallback(() => setOpen(false), []),
  );

  // Built on first open only: labelling ~400 zones means ~400 formatters.
  const options = useMemo(() => {
    if (!open) return [];
    const zones = zoneNames().map((zone) => {
      const offset = offsetLabel(zone);
      return { value: zone, label: offset ? `${zone} (${offset})` : zone };
    });
    zones.sort((a, b) => a.value.localeCompare(b.value));
    return [{ value: "", label: "No timezone set" }, ...zones];
  }, [open]);

  const effective = timezone || FALLBACK_ZONE;
  const now = open ? currentTimeIn(effective) : "";

  return (
    <div
      ref={rootRef}
      className="relative"
      onKeyDown={(e) => {
        if (open && e.key === "Escape") {
          e.preventDefault();
          setOpen(false);
          triggerRef.current?.focus();
        }
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((o) => !o)}
        title="Current time awareness"
        aria-label="Current time awareness"
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          "flex size-9 items-center justify-center rounded-lg border border-line bg-white text-sub transition-colors hover:bg-app cursor-pointer",
          open && "bg-app",
        )}
      >
        <Clock4 className="size-4" />
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Current time awareness"
          className="absolute right-0 top-full z-50 mt-1.5 w-[360px] rounded-xl border border-line bg-white p-4 shadow-lg shadow-black/5"
        >
          <div className="text-[14px] font-semibold text-ink">Current Time Awareness</div>
          <p className="mt-0.5 text-[12px] leading-snug text-sub">
            Set the agent&apos;s timezone so it understands the current local time and interprets
            time references correctly (for example &ldquo;today,&rdquo; &ldquo;tomorrow,&rdquo;
            &ldquo;in 2 hours,&rdquo; business hours, and scheduling windows).
          </p>
          <div className="mt-3">
            <Select
              value={timezone}
              onChange={onTimezone}
              options={options}
              className="w-full"
            />
          </div>
          <p className="mt-2 text-[12px] leading-snug text-faint">
            {timezone
              ? `{{current_time}} now resolves to ${now}.`
              : `No timezone set — time variables use ${FALLBACK_ZONE} (${now}).`}
          </p>
        </div>
      )}
    </div>
  );
}
