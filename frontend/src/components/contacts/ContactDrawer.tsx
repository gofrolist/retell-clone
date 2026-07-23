"use client";

import CallDrawer from "@/components/calls/CallDrawer";
import CustomFieldInputs, {
  formatCustomValue,
  type CustomFieldValues,
} from "@/components/contacts/CustomFieldInputs";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { Field, TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import StatusDot from "@/components/ui/StatusDot";
import Toggle from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { Call, Contact, ContactFieldDefinition } from "@/lib/types";
import { cn, formatDateTimeZone, formatDurationLong, pressableProps } from "@/lib/utils";
import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock,
  Pencil,
  PhoneIncoming,
  PhoneOutgoing,
  User,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

export const TIMEZONE_OPTIONS = [
  { value: "", label: "Not set" },
  { value: "America/New_York", label: "Eastern (New York)" },
  { value: "America/Chicago", label: "Central (Chicago)" },
  { value: "America/Denver", label: "Mountain (Denver)" },
  { value: "America/Phoenix", label: "Arizona (Phoenix)" },
  { value: "America/Los_Angeles", label: "Pacific (Los Angeles)" },
  { value: "America/Anchorage", label: "Alaska (Anchorage)" },
  { value: "Pacific/Honolulu", label: "Hawaii (Honolulu)" },
];

// Per-direction leg cap for the conversations fetch; when a contact has more
// calls than this the stats are computed over the latest slice and the UI
// says so ("Showing latest N").
const CALLS_FETCH_LIMIT = 100;

function InfoRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-2.5 text-[13px]">
      <span className="shrink-0 text-sub">{label}</span>
      <span className="min-w-0 text-right break-words">{children}</span>
    </div>
  );
}

function sentimentColor(s: Call["user_sentiment"]): "green" | "red" | "blue" | "gray" {
  return s === "Positive" ? "green" : s === "Negative" ? "red" : s === "Neutral" ? "blue" : "gray";
}

function titleCase(v: string): string {
  return v.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function ConversationCard({ call, onOpen }: { call: Call; onOpen: () => void }) {
  const d = new Date(call.start_timestamp);
  return (
    <div
      {...pressableProps(`Open call ${call.call_id}`, onOpen)}
      className="cursor-pointer rounded-xl border border-line bg-card p-3 transition-colors hover:border-line-strong hover:bg-app/40"
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full",
            call.direction === "outbound"
              ? "bg-orange-50 text-orange-500"
              : "bg-accent/10 text-accent",
          )}
        >
          {call.direction === "outbound" ? (
            <PhoneOutgoing className="size-3.5" />
          ) : (
            <PhoneIncoming className="size-3.5" />
          )}
        </span>
        <span className="text-[13px] font-semibold">
          {d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        </span>
        <span className="text-[12.5px] text-sub">
          {d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", timeZoneName: "short" })}
        </span>
        <span className="ml-auto flex items-center gap-1 text-[12.5px] text-sub">
          <Clock className="size-3.5" />
          {formatDurationLong(call.duration_ms)}
          <ChevronRight className="size-3.5 text-faint" />
        </span>
      </div>
      {call.call_summary && (
        <p className="mt-2 line-clamp-3 text-[12.5px] leading-relaxed text-sub">
          {call.call_summary}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
        <StatusDot
          color={call.call_successful ? "green" : call.call_successful === false ? "red" : "gray"}
          label={
            call.call_successful ? "Successful" : call.call_successful === false ? "Unsuccessful" : "Unknown"
          }
        />
        <StatusDot color={sentimentColor(call.user_sentiment)} label={call.user_sentiment} />
        {call.disconnection_reason && (
          <span className="text-sub">{titleCase(call.disconnection_reason)}</span>
        )}
      </div>
    </div>
  );
}

export default function ContactDrawer({
  contact,
  fieldDefs = [],
  onClose,
  onNavigate,
  onUpdated,
}: {
  contact: Contact;
  fieldDefs?: ContactFieldDefinition[];
  onClose: () => void;
  onNavigate: (dir: 1 | -1) => void;
  onUpdated: (contact: Contact) => void;
}) {
  const [calls, setCalls] = useState<Call[] | null>(null);
  const [callsError, setCallsError] = useState<string | null>(null);
  const [openCall, setOpenCall] = useState<Call | null>(null);

  // edit mode
  const [editing, setEditing] = useState(false);
  const [firstName, setFirstName] = useState(contact.first_name);
  const [lastName, setLastName] = useState(contact.last_name);
  const [timezone, setTimezone] = useState(contact.timezone ?? "");
  const [doNotCall, setDoNotCall] = useState(contact.do_not_call);
  const [externalId, setExternalId] = useState(contact.external_id ?? "");
  const [customValues, setCustomValues] = useState<CustomFieldValues>(contact.custom_fields ?? {});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const resetDraft = (c: Contact) => {
    setEditing(false);
    setFirstName(c.first_name);
    setLastName(c.last_name);
    setTimezone(c.timezone ?? "");
    setDoNotCall(c.do_not_call);
    setExternalId(c.external_id ?? "");
    setCustomValues(c.custom_fields ?? {});
    setSaveError(null);
  };

  // Reset edit state when navigating between contacts.
  useEffect(() => {
    resetDraft(contact);

  }, [contact]);

  // Conversations = calls where either leg is the contact's number. The
  // backend ANDs from_number/to_number filters, so query each leg and merge.
  // Cached per contact for the drawer's lifetime and debounced so holding
  // ArrowDown through the list doesn't fire two requests per keypress.
  const callsCache = useRef(new Map<string, Call[]>());
  useEffect(() => {
    let cancelled = false;
    setCallsError(null);
    setOpenCall(null);
    const hit = callsCache.current.get(contact.contact_id);
    if (hit) {
      setCalls(hit);
      return;
    }
    setCalls(null);
    const timer = setTimeout(() => {
      const leg = (key: "from_number" | "to_number") =>
        api.listCalls({
          limit: CALLS_FETCH_LIMIT,
          sort_order: "descending",
          filter_criteria: { [key]: [contact.phone_number] },
        });
      Promise.all([leg("from_number"), leg("to_number")])
        .then(([a, b]) => {
          const seen = new Set<string>();
          const merged = [...a.calls, ...b.calls]
            .filter((c) => (seen.has(c.call_id) ? false : (seen.add(c.call_id), true)))
            .sort((x, y) => y.start_timestamp - x.start_timestamp);
          callsCache.current.set(contact.contact_id, merged);
          if (!cancelled) setCalls(merged);
        })
        .catch((e: unknown) => {
          if (!cancelled)
            setCallsError(e instanceof Error ? e.message : "Failed to load conversations");
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [contact.contact_id, contact.phone_number]);

  // ↑/↓ moves between contacts, but not while a nested call drawer is open,
  // not while the user is typing in the edit form (arrows move the caret /
  // drive the native select there), and never from a form control.
  useEffect(() => {
    if (openCall) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      const inFormControl =
        !!t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable);
      if (e.key === "Escape") {
        // In edit mode Escape backs out of the edit, not the whole drawer —
        // closing would silently discard the draft.
        if (editing) resetDraft(contact);
        else onClose();
        return;
      }
      if (editing || inFormControl) return;
      if (e.key === "ArrowDown") onNavigate(1);
      if (e.key === "ArrowUp") onNavigate(-1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);

  }, [openCall, editing, contact, onNavigate, onClose]);

  const save = async () => {
    // PATCH only what actually changed — sending the full draft would
    // overwrite concurrent edits (made since the contacts list was fetched)
    // with this drawer's stale copies of the untouched fields.
    const delta: Parameters<typeof api.updateContact>[1] = {};
    if (firstName.trim() !== contact.first_name) delta.first_name = firstName.trim();
    if (lastName.trim() !== contact.last_name) delta.last_name = lastName.trim();
    if ((timezone || null) !== (contact.timezone ?? null)) delta.timezone = timezone || null;
    if (doNotCall !== contact.do_not_call) delta.do_not_call = doNotCall;
    if ((externalId.trim() || null) !== (contact.external_id ?? null))
      delta.external_id = externalId.trim() || null;
    const prevCustom = contact.custom_fields ?? {};
    const customChanged = fieldDefs.some(
      (d) => (customValues[d.key] ?? null) !== (prevCustom[d.key] ?? null),
    );
    if (customChanged) {
      // The API stores the whole dict; merge over the stored values so keys
      // from since-deleted definitions survive.
      delta.custom_fields = { ...prevCustom, ...customValues };
    }
    if (Object.keys(delta).length === 0) {
      setEditing(false);
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const updated = await api.updateContact(contact.contact_id, delta);
      // update-contact doesn't recompute conversation stats — keep ours.
      onUpdated({
        ...contact,
        ...updated,
        related_conversations: contact.related_conversations,
        latest_conversation: contact.latest_conversation,
      });
      setEditing(false);
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save contact");
    } finally {
      setSaving(false);
    }
  };

  const inbound = calls?.filter((c) => c.direction === "inbound").length ?? 0;
  const outbound = (calls?.length ?? 0) - inbound;
  const totalMs = calls?.reduce((sum, c) => sum + (c.duration_ms || 0), 0) ?? 0;
  const avgMs = calls && calls.length > 0 ? totalMs / calls.length : 0;
  // The server count is authoritative — the fetched list is capped per leg,
  // so its length must never override related_conversations.
  const conversationCount = contact.related_conversations;
  const truncated = calls !== null && calls.length < contact.related_conversations;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end bg-black/25"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Contact details"
        className="flex h-full w-full max-w-4xl flex-col bg-card shadow-2xl outline-none"
      >
        <div className="flex items-center gap-3 border-b border-line px-5 py-3.5">
          <span className="flex size-9 items-center justify-center rounded-full bg-app text-sub">
            <User className="size-4.5" strokeWidth={1.8} />
          </span>
          <h2 className="text-[15px] font-semibold tabular-nums">
            {[contact.first_name, contact.last_name].filter(Boolean).join(" ") ||
              contact.phone_number}
          </h2>
          <span className="ml-auto flex items-center gap-1 text-[13px] text-sub">
            Use
            <button
              onClick={() => onNavigate(-1)}
              className="rounded-md border border-line p-0.5 text-sub hover:bg-app cursor-pointer"
              aria-label="Previous contact"
            >
              <ChevronUp className="size-3.5" />
            </button>
            <button
              onClick={() => onNavigate(1)}
              className="rounded-md border border-line p-0.5 text-sub hover:bg-app cursor-pointer"
              aria-label="Next contact"
            >
              <ChevronDown className="size-3.5" />
            </button>
            to navigate
          </span>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-sub hover:bg-app cursor-pointer"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="flex min-h-0 grow">
          {/* Contact information */}
          <div className="w-[46%] min-w-0 shrink-0 overflow-y-auto border-r border-line px-5 py-4">
            <div className="mb-1 flex items-center justify-between">
              <h3 className="text-[14px] font-semibold">Contact information</h3>
              {!editing && (
                <button
                  onClick={() => setEditing(true)}
                  className="rounded-md border border-line p-1.5 text-sub hover:bg-app cursor-pointer"
                  aria-label="Edit contact"
                >
                  <Pencil className="size-3.5" />
                </button>
              )}
            </div>

            {editing ? (
              <div className="mt-3 space-y-4">
                <div className="grid grid-cols-2 gap-3">
                  <Field label="First Name">
                    <TextInput value={firstName} onChange={(e) => setFirstName(e.target.value)} />
                  </Field>
                  <Field label="Last Name">
                    <TextInput value={lastName} onChange={(e) => setLastName(e.target.value)} />
                  </Field>
                </div>
                <Field label="Timezone">
                  <Select
                    value={timezone}
                    onChange={setTimezone}
                    options={TIMEZONE_OPTIONS}
                    className="w-full"
                  />
                </Field>
                <Field label="External ID">
                  <TextInput value={externalId} onChange={(e) => setExternalId(e.target.value)} />
                </Field>
                <CustomFieldInputs
                  defs={fieldDefs}
                  values={customValues}
                  onChange={setCustomValues}
                />
                <div className="flex items-center justify-between">
                  <span className="text-[13px] font-medium">Do Not Call</span>
                  <Toggle checked={doNotCall} onChange={setDoNotCall} />
                </div>
                {saveError && <p className="text-[12.5px] text-bad">{saveError}</p>}
                <div className="flex justify-end gap-2 border-t border-line pt-3">
                  <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                  <Button size="sm" variant="primary" onClick={save} disabled={saving}>
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </div>
              </div>
            ) : (
              <div className="mt-2 divide-y divide-line/70">
                <InfoRow label="Phone Number">
                  <span className="tabular-nums">{contact.phone_number}</span>
                </InfoRow>
                <InfoRow label="First Name">{contact.first_name || "—"}</InfoRow>
                <InfoRow label="Last Name">{contact.last_name || "—"}</InfoRow>
                <InfoRow label="Timezone">{contact.timezone || "—"}</InfoRow>
                <InfoRow label="Contact ID">
                  <CopyId value={contact.contact_id} />
                </InfoRow>
                <InfoRow label="Related Conversations">
                  <span className="tabular-nums">{conversationCount}</span>
                </InfoRow>
                <InfoRow label="Latest Conversation">
                  {contact.latest_conversation ? formatDateTimeZone(contact.latest_conversation) : "—"}
                </InfoRow>
                <InfoRow label="Do Not Call">
                  <span className={cn(contact.do_not_call && "font-medium text-bad")}>
                    {contact.do_not_call ? "Yes" : "No"}
                  </span>
                </InfoRow>
                <InfoRow label="External ID">{contact.external_id || "—"}</InfoRow>
                {fieldDefs.map((d) => (
                  <InfoRow key={d.key} label={d.label}>
                    {formatCustomValue(d, contact.custom_fields?.[d.key])}
                  </InfoRow>
                ))}
              </div>
            )}
          </div>

          {/* Conversations */}
          <div className="min-w-0 grow overflow-y-auto px-5 py-4">
            <h3 className="text-[14px] font-semibold">
              Conversations ({conversationCount})
              {truncated && calls && (
                <span className="ml-2 text-[12px] font-normal text-sub">
                  showing latest {calls.length} — stats cover these only
                </span>
              )}
            </h3>

            {callsError && (
              <p className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
                {callsError}
              </p>
            )}
            {!calls && !callsError && (
              <p className="py-8 text-center text-[13px] text-sub">Loading conversations…</p>
            )}

            {calls && calls.length === 0 && (
              <p className="py-8 text-center text-[13px] text-sub">
                No conversations with this contact yet.
              </p>
            )}

            {calls && calls.length > 0 && (
              <>
                <div className="mt-3 grid grid-cols-2 gap-3">
                  <div className="rounded-xl border border-line px-3.5 py-2.5">
                    <div className="text-[12px] text-sub">Total Time</div>
                    <div className="mt-0.5 text-[15px] font-semibold tabular-nums">
                      {formatDurationLong(totalMs)}
                    </div>
                  </div>
                  <div className="rounded-xl border border-line px-3.5 py-2.5">
                    <div className="text-[12px] text-sub">Avg/Call</div>
                    <div className="mt-0.5 text-[15px] font-semibold tabular-nums">
                      {formatDurationLong(avgMs)}
                    </div>
                  </div>
                </div>

                <div className="mt-3">
                  <div className="flex h-1.5 overflow-hidden rounded-full bg-app">
                    {inbound > 0 && (
                      <div className="bg-accent" style={{ width: `${(inbound / calls.length) * 100}%` }} />
                    )}
                    {outbound > 0 && (
                      <div className="bg-orange-400" style={{ width: `${(outbound / calls.length) * 100}%` }} />
                    )}
                  </div>
                  <div className="mt-1.5 flex items-center gap-4 text-[12px] text-sub">
                    <span className="flex items-center gap-1.5">
                      <span className="size-2 rounded-[3px] bg-accent" /> Inbound {inbound}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-2 rounded-[3px] bg-orange-400" /> Outbound {outbound}
                    </span>
                    <span className="flex items-center gap-1.5">
                      <span className="size-2 rounded-[3px] bg-faint" /> Message 0
                    </span>
                  </div>
                </div>

                <div className="mt-4 space-y-2.5 pb-6">
                  {calls.map((c) => (
                    <ConversationCard key={c.call_id} call={c} onOpen={() => setOpenCall(c)} />
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {openCall && calls && (
        <CallDrawer
          call={openCall}
          onClose={() => setOpenCall(null)}
          onNavigate={(dir) => {
            const idx = calls.findIndex((c) => c.call_id === openCall.call_id);
            const next = calls[idx + dir];
            if (next) setOpenCall(next);
          }}
          onUpdated={(updated) =>
            setCalls((cur) =>
              (cur ?? []).map((c) => (c.call_id === updated.call_id ? updated : c)),
            )
          }
        />
      )}
    </div>
  );
}
