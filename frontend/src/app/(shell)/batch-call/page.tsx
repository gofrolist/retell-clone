"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { PhoneNumber } from "@/lib/types";
import { cn, isE164 } from "@/lib/utils";
import {
  CalendarClock,
  CheckCircle2,
  Download,
  Info,
  Minus,
  Plus,
  Send,
  UploadCloud,
  Users,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

const TEMPLATE_CSV =
  "to_number,first_name,appointment_time\n" +
  "+14155550123,Alice,2026-07-15 10:00\n" +
  '+14155550124,"Bob, Jr.",2026-07-15 14:30\n';

interface Recipient {
  row: number; // 1-based CSV data row (excluding header)
  to_number: string;
  dynamic_variables: Record<string, string>;
  error?: string;
}

/** Minimal CSV parser: comma-separated, quoted cells with "" escapes, CR/LF. */
function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cell += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      rows.push(row);
      row = [];
    } else {
      cell += ch;
    }
  }
  if (cell !== "" || row.length > 0) {
    row.push(cell);
    rows.push(row);
  }
  return rows.filter((r) => r.some((c) => c.trim() !== ""));
}

function recipientsFromCsv(text: string): { recipients: Recipient[]; error?: string } {
  const rows = parseCsv(text);
  if (rows.length < 2) {
    return { recipients: [], error: "CSV needs a header row and at least one recipient row." };
  }
  const headers = rows[0].map((h) => h.trim());
  const toIdx = headers.findIndex((h) => h.toLowerCase() === "to_number");
  if (toIdx === -1) {
    return { recipients: [], error: 'CSV must have a "to_number" column.' };
  }
  const recipients = rows.slice(1).map((cells, i): Recipient => {
    const to = (cells[toIdx] ?? "").trim();
    const vars: Record<string, string> = {};
    headers.forEach((h, col) => {
      if (col === toIdx || !h) return;
      const v = (cells[col] ?? "").trim();
      if (v) vars[h] = v;
    });
    return {
      row: i + 1,
      to_number: to,
      dynamic_variables: vars,
      error: isE164(to) ? undefined : `"${to || "(empty)"}" is not a valid E.164 number`,
    };
  });
  return { recipients };
}

function downloadTemplate() {
  const blob = new Blob([TEMPLATE_CSV], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "batch-call-template.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export default function BatchCallPage() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [timing, setTiming] = useState<"now" | "schedule">("now");
  const [scheduleAt, setScheduleAt] = useState("");
  const concurrency = 5; // no backend field yet — stepper disabled
  const [recipients, setRecipients] = useState<Recipient[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [csvError, setCsvError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    api
      .listPhoneNumbers()
      .then((list) => {
        setNumbers(list);
        setFrom((f) => f || (list[0]?.phone_number ?? ""));
      })
      .catch(() => setNumbers([]));
  }, []);

  async function onFile(file: File) {
    setBatchId(null);
    setSubmitError(null);
    const text = await file.text();
    const { recipients: parsed, error } = recipientsFromCsv(text);
    setFileName(file.name);
    setRecipients(parsed);
    setCsvError(error ?? null);
  }

  const invalid = recipients.filter((r) => r.error);
  const canSend =
    Boolean(from) &&
    recipients.length > 0 &&
    invalid.length === 0 &&
    !submitting &&
    (timing === "now" || Boolean(scheduleAt));

  async function send() {
    setSubmitting(true);
    setSubmitError(null);
    setBatchId(null);
    try {
      const res = await api.createBatchCall({
        from_number: from,
        ...(name.trim() ? { name: name.trim() } : {}),
        tasks: recipients.map((r) => ({
          to_number: r.to_number,
          ...(Object.keys(r.dynamic_variables).length
            ? { retell_llm_dynamic_variables: r.dynamic_variables }
            : {}),
        })),
        ...(timing === "schedule" && scheduleAt
          ? { trigger_timestamp: new Date(scheduleAt).getTime() }
          : {}),
      });
      setBatchId(res.batch_call_id);
    } catch (e: unknown) {
      setSubmitError(e instanceof Error ? e.message : "Failed to create batch call");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex h-full overflow-hidden">
      <div className="min-w-0 grow overflow-y-auto px-8 py-6">
        <h1 className="text-[17px] font-semibold">Create a Batch Call</h1>

        <div className="mt-5 max-w-2xl space-y-5">
          <Field label="Batch Call Name">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. July reactivation wave 2"
            />
          </Field>

          <Field label="From Number">
            <Select
              value={from}
              onChange={setFrom}
              className="w-full"
              options={
                numbers.length
                  ? numbers.map((n) => ({
                      value: n.phone_number,
                      label: n.nickname ? `${n.nickname} (${n.phone_number})` : n.phone_number,
                    }))
                  : [{ value: "", label: "No phone numbers connected" }]
              }
            />
          </Field>

          <Field label="Recipients">
            <input
              ref={fileInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void onFile(file);
                e.target.value = "";
              }}
            />
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                const file = e.dataTransfer.files?.[0];
                if (file) void onFile(file);
              }}
              className="flex cursor-pointer flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line-strong bg-app/50 px-4 py-9 text-center hover:border-ink/40"
            >
              <UploadCloud className="size-6 text-faint" strokeWidth={1.5} />
              <div className="text-[13.5px] font-medium">
                {fileName ? (
                  <>
                    {fileName} — {recipients.length} recipient{recipients.length === 1 ? "" : "s"}
                  </>
                ) : (
                  <>
                    Drop your CSV here, or <span className="text-accent-deep">browse</span>
                  </>
                )}
              </div>
              <p className="text-xs text-sub">Up to 25MB, .csv files only</p>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  downloadTemplate();
                }}
                className="mt-1 inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
              >
                <Download className="size-3.5" />
                Download the template
              </button>
            </div>
            {csvError && <p className="mt-1.5 text-[13px] text-bad">{csvError}</p>}
            {invalid.length > 0 && (
              <p className="mt-1.5 text-[13px] text-bad">
                {invalid.length} row{invalid.length === 1 ? "" : "s"} with invalid phone numbers —
                fix the CSV and re-upload.
              </p>
            )}
          </Field>

          <div className="grid grid-cols-2 gap-3">
            {(
              [
                { key: "now", title: "Send Now", desc: "Start calling immediately.", icon: Send },
                { key: "schedule", title: "Schedule", desc: "Pick a start date and time.", icon: CalendarClock },
              ] as const
            ).map((t) => (
              <button
                key={t.key}
                onClick={() => setTiming(t.key)}
                className={cn(
                  "rounded-xl border p-4 text-left transition-colors cursor-pointer",
                  timing === t.key
                    ? "border-ink ring-1 ring-ink"
                    : "border-line hover:border-line-strong",
                )}
              >
                <t.icon className="mb-2 size-4.5 text-sub" strokeWidth={1.8} />
                <div className="text-[13.5px] font-semibold">{t.title}</div>
                <div className="mt-0.5 text-xs text-sub">{t.desc}</div>
              </button>
            ))}
          </div>

          {timing === "schedule" && (
            <Field label="Start At">
              <input
                type="datetime-local"
                value={scheduleAt}
                onChange={(e) => setScheduleAt(e.target.value)}
                className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
            </Field>
          )}

          <div className="flex items-center justify-between rounded-lg border border-line bg-white px-4 py-3">
            <div>
              <div className="text-[13px] font-medium">When Calls Can Run</div>
              <div className="text-xs text-sub">00:00 - 23:59, Mon - Sun</div>
            </div>
            <Button size="sm" disabled title="Not available yet">
              Edit
            </Button>
          </div>

          <Field
            label="Reserved Concurrency"
            hint="How many concurrent calls this batch may use."
          >
            <div className="inline-flex items-center rounded-lg border border-line bg-white" title="Not available yet">
              <button
                disabled
                className="flex size-9 items-center justify-center text-faint cursor-not-allowed"
                aria-label="Decrease"
              >
                <Minus className="size-3.5" />
              </button>
              <span className="w-12 text-center text-[13.5px] font-medium tabular-nums text-sub">
                {concurrency}
              </span>
              <button
                disabled
                className="flex size-9 items-center justify-center text-faint cursor-not-allowed"
                aria-label="Increase"
              >
                <Plus className="size-3.5" />
              </button>
            </div>
          </Field>

          <div className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[13px] text-accent-deep">
            <Info className="size-4 shrink-0" />
            Concurrency allocated to batch calling: 15
          </div>

          <p className="text-xs text-sub">
            By sending, you confirm recipients have consented to receive automated calls
            and that this batch complies with applicable Terms of Service and local
            regulations.
          </p>

          {submitError && <p className="text-[13px] text-bad">{submitError}</p>}
          {batchId && (
            <div className="flex items-center gap-2 rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-[13px] text-green-700">
              <CheckCircle2 className="size-4 shrink-0" />
              Batch call {timing === "schedule" ? "scheduled" : "sent"} — ID:{" "}
              <span className="font-medium">{batchId}</span>
            </div>
          )}

          <div className="flex items-center gap-2 pb-6">
            <Button disabled title="Drafts not available yet">
              Save as draft
            </Button>
            <Button variant="primary" disabled={!canSend} onClick={send}>
              {submitting ? "Sending…" : "Send"}
            </Button>
          </div>
        </div>
      </div>

      {/* recipients panel */}
      <aside className="hidden w-80 shrink-0 flex-col border-l border-line bg-app xl:flex">
        <div className="border-b border-line px-4 py-3.5 text-[13.5px] font-semibold">
          Recipients{recipients.length > 0 && ` (${recipients.length})`}
        </div>
        {recipients.length === 0 ? (
          <div className="flex grow flex-col items-center justify-center gap-2 px-6 text-center">
            <Users className="size-6 text-faint" strokeWidth={1.5} />
            <p className="text-[13px] text-sub">Please upload recipients first.</p>
          </div>
        ) : (
          <div className="min-h-0 grow space-y-1.5 overflow-y-auto p-3">
            {recipients.map((r) => (
              <div
                key={r.row}
                className={cn(
                  "rounded-lg border bg-white px-3 py-2 text-[12.5px]",
                  r.error ? "border-red-200" : "border-line",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="grow truncate font-medium tabular-nums">
                    {r.to_number || "(empty)"}
                  </span>
                  {r.error && <X className="size-3.5 shrink-0 text-bad" />}
                </div>
                {r.error ? (
                  <div className="mt-0.5 text-[11.5px] text-bad">Row {r.row}: {r.error}</div>
                ) : (
                  Object.keys(r.dynamic_variables).length > 0 && (
                    <div className="mt-0.5 truncate text-[11.5px] text-sub">
                      {Object.entries(r.dynamic_variables)
                        .map(([k, v]) => `${k}: ${v}`)
                        .join(" · ")}
                    </div>
                  )
                )}
              </div>
            ))}
          </div>
        )}
      </aside>
    </div>
  );
}
