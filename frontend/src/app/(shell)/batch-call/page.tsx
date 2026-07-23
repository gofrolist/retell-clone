"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { CheckboxRow } from "@/components/ui/RadioRow";
import Select from "@/components/ui/Select";
import { api, type BatchCallDraft, type CallTimeWindow } from "@/lib/api";
import type { PhoneNumber } from "@/lib/types";
import { cn, isE164, triggerBlobDownload } from "@/lib/utils";
import {
  CalendarClock,
  CheckCircle2,
  Download,
  FileText,
  Info,
  Minus,
  Plus,
  Send,
  Trash2,
  UploadCloud,
  Users,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

const ALL_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];
const DAY_LABEL: Record<string, string> = {
  mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun",
};
const DEFAULT_WINDOW: CallTimeWindow = { start: "00:00", end: "23:59", days: ALL_DAYS };

function windowLabel(w: CallTimeWindow): string {
  const days =
    w.days.length === 7
      ? "Mon - Sun"
      : w.days.map((d) => DAY_LABEL[d] ?? d).join(", ");
  return `${w.start} - ${w.end}, ${days || "no days"}`;
}

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
  triggerBlobDownload(new Blob([TEMPLATE_CSV], { type: "text/csv" }), "batch-call-template.csv");
}

export default function BatchCallPage() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [name, setName] = useState("");
  const [from, setFrom] = useState("");
  const [timing, setTiming] = useState<"now" | "schedule">("now");
  const [scheduleAt, setScheduleAt] = useState("");
  const [concurrency, setConcurrency] = useState(5);
  const [window_, setWindow] = useState<CallTimeWindow>(DEFAULT_WINDOW);
  const [windowOpen, setWindowOpen] = useState(false);
  const [windowDraft, setWindowDraft] = useState<CallTimeWindow>(DEFAULT_WINDOW);
  const [allocated, setAllocated] = useState<number | null>(null);
  const [drafts, setDrafts] = useState<BatchCallDraft[]>([]);
  const [savingDraft, setSavingDraft] = useState(false);
  const [draftSaved, setDraftSaved] = useState(false);
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
    // Real number for the "concurrency allocated to batch calling" banner:
    // the workspace limit minus slots reserved for inbound.
    api
      .getConcurrency()
      .then((c) => setAllocated(Math.max(0, c.concurrency_limit - c.reserved_inbound_concurrency)))
      .catch(() => setAllocated(null));
    api.listBatchCallDrafts().then(setDrafts).catch(() => setDrafts([]));
  }, []);

  const loadDraft = (d: BatchCallDraft) => {
    setName(d.name ?? "");
    if (d.from_number) setFrom(d.from_number);
    setConcurrency(d.reserved_concurrency ?? 5);
    setWindow(d.call_time_window ?? DEFAULT_WINDOW);
    if (d.trigger_timestamp) {
      setTiming("schedule");
      const dt = new Date(d.trigger_timestamp);
      const p = (n: number) => String(n).padStart(2, "0");
      setScheduleAt(
        `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}T${p(dt.getHours())}:${p(dt.getMinutes())}`,
      );
    } else {
      setTiming("now");
      setScheduleAt("");
    }
    setRecipients(
      (d.tasks ?? []).map((t, i) => ({
        row: i + 1,
        to_number: t.to_number,
        dynamic_variables: t.retell_llm_dynamic_variables ?? {},
        error: isE164(t.to_number) ? undefined : `"${t.to_number}" is not a valid E.164 number`,
      })),
    );
    setFileName(d.name ? `Draft: ${d.name}` : "Draft");
    setBatchId(null);
    setSubmitError(null);
  };

  const saveDraft = async () => {
    setSavingDraft(true);
    setSubmitError(null);
    try {
      const draft = await api.saveBatchCallDraft({
        name: name.trim() || null,
        from_number: from || null,
        tasks: recipients
          .filter((r) => !r.error)
          .map((r) => ({
            to_number: r.to_number,
            ...(Object.keys(r.dynamic_variables).length
              ? { retell_llm_dynamic_variables: r.dynamic_variables }
              : {}),
          })),
        trigger_timestamp:
          timing === "schedule" && scheduleAt ? new Date(scheduleAt).getTime() : null,
        reserved_concurrency: concurrency,
        call_time_window: window_,
      });
      setDrafts((cur) => [draft, ...cur]);
      setDraftSaved(true);
      setTimeout(() => setDraftSaved(false), 2000);
    } catch (e) {
      setSubmitError(e instanceof Error ? e.message : "Failed to save draft");
    } finally {
      setSavingDraft(false);
    }
  };

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
        reserved_concurrency: concurrency,
        call_time_window: window_,
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
              <div className="text-xs text-sub">{windowLabel(window_)}</div>
            </div>
            <Button
              size="sm"
              onClick={() => {
                setWindowDraft(window_);
                setWindowOpen(true);
              }}
            >
              Edit
            </Button>
          </div>

          <Field
            label="Reserved Concurrency"
            hint="Concurrency slots held back for non-batch calls while this batch runs."
          >
            <div className="inline-flex items-center rounded-lg border border-line bg-white">
              <button
                onClick={() => setConcurrency((c) => Math.max(0, c - 1))}
                disabled={concurrency <= 0}
                className="flex size-9 items-center justify-center text-sub hover:text-ink cursor-pointer disabled:text-faint disabled:cursor-not-allowed"
                aria-label="Decrease"
              >
                <Minus className="size-3.5" />
              </button>
              <span className="w-12 text-center text-[13.5px] font-medium tabular-nums">
                {concurrency}
              </span>
              <button
                onClick={() => setConcurrency((c) => Math.min(allocated ?? 500, c + 1))}
                className="flex size-9 items-center justify-center text-sub hover:text-ink cursor-pointer"
                aria-label="Increase"
              >
                <Plus className="size-3.5" />
              </button>
            </div>
          </Field>

          <div className="flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[13px] text-accent-deep">
            <Info className="size-4 shrink-0" />
            Concurrency allocated to batch calling: {allocated ?? "—"}
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

          <div className="flex items-center gap-2 pb-2">
            <Button onClick={saveDraft} disabled={savingDraft || (!name.trim() && !from)}>
              {savingDraft ? "Saving…" : draftSaved ? "Draft saved" : "Save as draft"}
            </Button>
            <Button variant="primary" disabled={!canSend} onClick={send}>
              {submitting ? "Sending…" : "Send"}
            </Button>
          </div>

          {drafts.length > 0 && (
            <div className="pb-6">
              <h2 className="mb-2 text-[13.5px] font-semibold">Drafts</h2>
              <div className="divide-y divide-line rounded-lg border border-line bg-white">
                {drafts.map((d) => (
                  <div key={d.batch_call_id} className="flex items-center gap-2.5 px-3 py-2.5">
                    <FileText className="size-4 shrink-0 text-sub" />
                    <div className="min-w-0 grow">
                      <div className="truncate text-[13px] font-medium">
                        {d.name || "Untitled draft"}
                      </div>
                      <div className="truncate text-xs text-sub">
                        {d.from_number || "no from number"} · {d.tasks.length} recipient
                        {d.tasks.length === 1 ? "" : "s"} ·{" "}
                        {new Date(d.created_at_ms).toLocaleString()}
                      </div>
                    </div>
                    <Button size="sm" variant="ghost" onClick={() => loadDraft(d)}>
                      Load
                    </Button>
                    <button
                      onClick={async () => {
                        try {
                          await api.deleteBatchCallDraft(d.batch_call_id);
                          setDrafts((cur) =>
                            cur.filter((x) => x.batch_call_id !== d.batch_call_id),
                          );
                        } catch {
                          // keep the row on failure
                        }
                      }}
                      className="rounded-md p-1.5 text-sub hover:bg-app hover:text-bad cursor-pointer"
                      aria-label="Delete draft"
                    >
                      <Trash2 className="size-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      <Modal
        open={windowOpen}
        onClose={() => setWindowOpen(false)}
        title="When Calls Can Run"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setWindowOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={windowDraft.days.length === 0}
              onClick={() => {
                setWindow(windowDraft);
                setWindowOpen(false);
              }}
            >
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Field label="From" className="grow">
              <input
                type="time"
                value={windowDraft.start}
                onChange={(e) => setWindowDraft((w) => ({ ...w, start: e.target.value }))}
                className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none focus:border-accent"
              />
            </Field>
            <Field label="To" className="grow">
              <input
                type="time"
                value={windowDraft.end}
                onChange={(e) => setWindowDraft((w) => ({ ...w, end: e.target.value }))}
                className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none focus:border-accent"
              />
            </Field>
          </div>
          <Field label="Days">
            <div className="grid grid-cols-2 gap-x-4">
              {ALL_DAYS.map((d) => (
                <CheckboxRow
                  key={d}
                  checked={windowDraft.days.includes(d)}
                  onChange={(v) =>
                    setWindowDraft((w) => ({
                      ...w,
                      days: v
                        ? ALL_DAYS.filter((x) => w.days.includes(x) || x === d)
                        : w.days.filter((x) => x !== d),
                    }))
                  }
                  label={DAY_LABEL[d]}
                />
              ))}
            </div>
          </Field>
          <p className="text-[12px] text-faint">
            Calls outside this window stay queued until the window opens.
          </p>
        </div>
      </Modal>

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
