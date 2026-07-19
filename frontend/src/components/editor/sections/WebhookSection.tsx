"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Slider from "@/components/ui/Slider";
import { api } from "@/lib/api";
import { useClickOutside } from "@/lib/useClickOutside";
import { CheckCircle2, Loader2, Settings2, XCircle } from "lucide-react";
import { useCallback, useRef, useState } from "react";

// The full Retell webhook-event catalog (mirror of the backend's
// WEBHOOK_EVENT_TYPES). null selection means "all of them". The worker
// currently fires only the call_* events; the transcript/transfer events are
// selectable for Retell parity and deliver once the worker emits them.
const EVENT_CATALOG = [
  { id: "call_started", label: "Call started" },
  { id: "call_ended", label: "Call ended" },
  { id: "call_analyzed", label: "Call analyzed" },
  { id: "transcript_updated", label: "Transcript updated" },
  { id: "transfer_started", label: "Transfer started" },
  { id: "transfer_bridged", label: "Transfer bridged" },
  { id: "transfer_cancelled", label: "Transfer cancelled" },
  { id: "transfer_ended", label: "Transfer ended" },
] as const;
const ALL_EVENT_IDS = EVENT_CATALOG.map((e) => e.id) as string[];
// Default subscription when the agent hasn't customized events: the call_*
// events the worker actually fires today. The transfer/transcript events start
// unchecked (they don't emit yet) but can be opted into.
const DEFAULT_EVENT_IDS = ["call_started", "call_ended", "call_analyzed"];

const DEFAULT_TIMEOUT_MS = 5000;

type TestResult = { ok: boolean; status_code: number | null; error: string | null };

export default function WebhookSection({
  agentId,
  url,
  onUrl,
  timeoutMs,
  onTimeoutMs,
  events,
  onEvents,
}: {
  agentId: string;
  url: string;
  onUrl: (v: string) => void;
  timeoutMs: number;
  onTimeoutMs: (v: number) => void;
  events: string[] | null;
  onEvents: (v: string[]) => void;
}) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  const selected = events ?? DEFAULT_EVENT_IDS;

  const runTest = async () => {
    if (!url.trim() || testing) return;
    setTesting(true);
    setResult(null);
    try {
      setResult(
        await api.testAgentWebhook(agentId, {
          webhook_url: url,
          webhook_timeout_ms: timeoutMs,
          event: "call_ended",
        }),
      );
    } catch (e) {
      setResult({
        ok: false,
        status_code: null,
        error: e instanceof Error ? e.message : "Test failed",
      });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-5">
      <Field
        label="Agent Level Webhook URL"
        hint="Receives the selected events, signed with x-retell-signature."
      >
        <div className="flex items-center gap-2">
          <TextInput
            value={url}
            onChange={(e) => {
              onUrl(e.target.value);
              setResult(null);
            }}
            placeholder="https://your-server.com/webhook"
          />
          <Button
            onClick={runTest}
            disabled={!url.trim() || testing}
            title={url.trim() ? "Send a sample event to this URL" : "Enter a URL first"}
          >
            {testing ? <Loader2 className="size-4 animate-spin" /> : "Test"}
          </Button>
        </div>
        {result && (
          <p
            className={
              "mt-1.5 flex items-center gap-1.5 text-xs " +
              (result.ok ? "text-green-700" : "text-bad")
            }
          >
            {result.ok ? (
              <CheckCircle2 className="size-3.5 shrink-0" />
            ) : (
              <XCircle className="size-3.5 shrink-0" />
            )}
            {result.ok
              ? `Delivered${result.status_code ? ` (${result.status_code})` : ""}`
              : `Failed: ${result.error ?? "unknown error"}`}
          </p>
        )}
      </Field>

      <Slider
        label="Webhook Timeout"
        min={1}
        max={30}
        step={1}
        value={Math.round((timeoutMs || DEFAULT_TIMEOUT_MS) / 1000)}
        onChange={(v) => onTimeoutMs(v * 1000)}
        format={(v) => `${v}s`}
      />

      <WebhookEventsField selected={selected} onChange={onEvents} />
    </div>
  );
}

function WebhookEventsField({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(
    ref,
    useCallback(() => setOpen(false), []),
  );

  const toggle = (id: string) => {
    const next = selected.includes(id)
      ? selected.filter((e) => e !== id)
      : // keep catalog order so the stored list is stable
        ALL_EVENT_IDS.filter((e) => e === id || selected.includes(e));
    onChange(next);
  };

  const summary =
    selected.length === ALL_EVENT_IDS.length
      ? "All events"
      : selected.length === 0
        ? "No events"
        : `${selected.length} of ${ALL_EVENT_IDS.length} events`;

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <label className="text-[13px] font-medium text-ink">Webhook Events</label>
        <div ref={ref} className="relative">
          <Button size="sm" onClick={() => setOpen((v) => !v)}>
            <Settings2 className="size-3.5" />
            Set Up
          </Button>
          {open && (
            <div className="absolute right-0 top-full z-20 mt-1 w-56 rounded-lg border border-line bg-white p-1.5 shadow-lg">
              <p className="px-2 pb-1.5 pt-1 text-xs text-sub">
                Choose which events this webhook receives.
              </p>
              {EVENT_CATALOG.map((ev) => (
                <label
                  key={ev.id}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] hover:bg-app"
                >
                  <input
                    type="checkbox"
                    className="size-4 accent-accent"
                    checked={selected.includes(ev.id)}
                    onChange={() => toggle(ev.id)}
                  />
                  {ev.label}
                </label>
              ))}
            </div>
          )}
        </div>
      </div>
      <p className="text-xs text-sub">{summary}</p>
    </div>
  );
}
