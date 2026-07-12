"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { RadioRow } from "@/components/ui/RadioRow";
import Select from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { Alert } from "@/lib/types";
import { withValue } from "@/lib/utils";
import { Plus, X } from "lucide-react";
import { useEffect, useState } from "react";

const CHECK_EVERY_OPTIONS = [
  { value: "5", label: "5 min" },
  { value: "15", label: "15 min" },
  { value: "60", label: "1 hour" },
];

const LOOKBACK_OPTIONS = [
  { value: "30", label: "30 min" },
  { value: "60", label: "1 hour" },
  { value: "1440", label: "24 hours" },
];

// Backend stores metric/condition strings verbatim; keep the UI labels.
const METRIC_OPTIONS = [
  { value: "calls", label: "Number of Calls" },
  { value: "failed", label: "Failed Calls" },
  { value: "duration", label: "Average Duration" },
  { value: "latency", label: "Average Latency" },
];

export default function CreateAlertModal({
  open,
  onClose,
  editing,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  /** When set, the modal edits this alert instead of creating a new one. */
  editing?: Alert | null;
  onSaved: () => void;
}) {
  const [name, setName] = useState("");
  const [checkEvery, setCheckEvery] = useState("5");
  const [lookback, setLookback] = useState("30");
  const [metric, setMetric] = useState("calls");
  const [condition, setCondition] = useState("above");
  const [threshold, setThreshold] = useState("2");
  const [emails, setEmails] = useState<string[]>([]);
  const [emailDraft, setEmailDraft] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seed the form each time the modal opens (create vs. edit prefill).
  useEffect(() => {
    if (!open) return;
    setName(editing?.name ?? "");
    setCheckEvery(String(editing?.check_every_min ?? 5));
    setLookback(String(editing?.lookback_min ?? 30));
    setMetric(editing?.metric ?? "calls");
    setCondition(editing?.condition ?? "above");
    setThreshold(String(editing?.threshold ?? 2));
    setEmails(editing?.notify_emails ?? []);
    setEmailDraft("");
    setWebhookUrl(editing?.webhook_url ?? "");
    setError(null);
  }, [open, editing]);

  const addEmail = () => {
    const email = emailDraft.trim();
    if (!email || emails.includes(email)) return;
    setEmails((cur) => [...cur, email]);
    setEmailDraft("");
  };

  const save = async () => {
    setSubmitting(true);
    setError(null);
    const body = {
      name: name.trim(),
      metric,
      condition,
      threshold: Number(threshold) || 0,
      check_every_min: Number(checkEvery) || 5,
      lookback_min: Number(lookback) || 30,
      notify_emails: emails,
      // null (not undefined) so PATCH can clear a previously set URL —
      // JSON.stringify drops undefined keys entirely.
      webhook_url: (webhookUrl.trim() || null) as string | undefined,
    };
    try {
      if (editing) {
        await api.updateAlert(editing.alert_id, body);
      } else {
        await api.createAlert({ ...body, enabled: true });
      }
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save alert");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={editing ? "Edit Alert" : "Create Alert"}
      width="max-w-xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={submitting || !name.trim()}>
            {submitting ? "Saving…" : editing ? "Save" : "Create"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Alert Name">
          <TextInput
            placeholder="e.g. Call volume spike"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          Check every
          <Select
            value={checkEvery}
            onChange={setCheckEvery}
            options={withValue(CHECK_EVERY_OPTIONS, checkEvery)}
          />
          for the last
          <Select
            value={lookback}
            onChange={setLookback}
            options={withValue(LOOKBACK_OPTIONS, lookback)}
          />
        </div>

        <div>
          <RadioRow checked onSelect={() => {}} label="Compare to certain value" />
          <div title="Not available yet" className="opacity-50">
            <div className="pointer-events-none">
              <RadioRow checked={false} onSelect={() => {}} label="Compare to last cycle" />
            </div>
          </div>
        </div>

        <Field label="Metric">
          <Select
            value={metric}
            onChange={setMetric}
            className="w-full"
            options={withValue(METRIC_OPTIONS, metric)}
          />
        </Field>

        <div className="flex flex-wrap items-center gap-2 text-[13px]">
          when sum
          <Select
            value={condition}
            onChange={setCondition}
            options={withValue(
              [
                { value: "above", label: "is above" },
                { value: "below", label: "is below" },
              ],
              condition,
            )}
          />
          <TextInput
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            className="w-20 text-center"
            inputMode="decimal"
          />
        </div>

        <div className="rounded-lg border border-line bg-app/50 p-3">
          <Field label="Notify via Email">
            <div className="flex items-center gap-2">
              <TextInput
                placeholder="ops@company.com"
                value={emailDraft}
                onChange={(e) => setEmailDraft(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addEmail()}
              />
              <Button size="sm" onClick={addEmail} disabled={!emailDraft.trim()}>
                <Plus className="size-3.5" /> Add
              </Button>
            </div>
            {emails.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {emails.map((email) => (
                  <span
                    key={email}
                    className="inline-flex items-center gap-1 rounded-full border border-line bg-white px-2 py-0.5 text-xs"
                  >
                    {email}
                    <button
                      onClick={() => setEmails((cur) => cur.filter((x) => x !== email))}
                      className="text-faint hover:text-ink cursor-pointer"
                      aria-label={`Remove ${email}`}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </Field>
          <Field label="Webhook URL" className="mt-3">
            <div className="flex items-center gap-2">
              <TextInput
                placeholder="https://hooks.company.com/alerts"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
              />
              <Button size="sm" disabled title="Not available yet">
                Test
              </Button>
            </div>
          </Field>
        </div>

        {error && <p className="text-[12.5px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
