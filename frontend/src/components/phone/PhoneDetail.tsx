"use client";

import AgentCard, { CountryTags, WebhookCheckbox } from "./AgentCard";
import Button from "@/components/ui/Button";
import CopyId from "@/components/ui/CopyId";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { api } from "@/lib/api";
import type { Agent, PhoneNumber } from "@/lib/types";
import { isE164 } from "@/lib/utils";
import { ChevronRight, Pencil, PhoneOutgoing, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

const ADD_ONS = [
  { name: "SMS", desc: "The ability to send SMS", action: "Setup SMS Function" },
  {
    name: "Verified Phone Number",
    desc: 'Set up verification to prevent your phone number from being marked as "Spam Likely". ($10.00/month - U.S. numbers only)',
    action: "Set Up",
  },
  {
    name: "Branded Call",
    desc: "Display your verified business name as the caller ID. ($0.1/outbound call - U.S. numbers only)",
    action: "Set Up",
  },
];

export default function PhoneDetail({
  phone,
  agents,
  onChanged,
  onDeleted,
}: {
  phone: PhoneNumber;
  agents: Agent[];
  onChanged: () => void;
  onDeleted: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [nick, setNick] = useState(phone.nickname ?? "");
  const [fallback, setFallback] = useState(phone.fallback_number ?? "");
  const [fallbackError, setFallbackError] = useState<string | null>(null);
  const [fallbackSaved, setFallbackSaved] = useState(false);
  const [callOpen, setCallOpen] = useState(false);
  const [callTo, setCallTo] = useState("");
  const [calling, setCalling] = useState(false);
  const [callResult, setCallResult] = useState<string | null>(null);
  const [callError, setCallError] = useState<string | null>(null);

  useEffect(() => {
    setNick(phone.nickname ?? "");
    setRenaming(false);
    setError(null);
    setFallback(phone.fallback_number ?? "");
    setFallbackError(null);
    setFallbackSaved(false);
  }, [phone.phone_number, phone.nickname, phone.fallback_number]);

  async function update(body: Record<string, unknown>) {
    setError(null);
    try {
      await api.updatePhoneNumber(phone.phone_number, body);
      onChanged();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to update phone number");
    }
  }

  async function saveNickname() {
    setRenaming(false);
    const next = nick.trim();
    if (next === (phone.nickname ?? "")) return;
    await update({ nickname: next || null });
  }

  async function saveFallback() {
    const next = fallback.trim();
    if (next === (phone.fallback_number ?? "")) return;
    if (next && !isE164(next)) {
      setFallbackError("Enter a valid E.164 number, e.g. +14155550123");
      return;
    }
    setFallbackError(null);
    await update({ fallback_number: next || null });
    setFallbackSaved(true);
    setTimeout(() => setFallbackSaved(false), 2000);
  }

  async function remove() {
    if (!window.confirm(`Delete ${phone.phone_number}? This cannot be undone.`)) return;
    setError(null);
    try {
      await api.deletePhoneNumber(phone.phone_number);
      onDeleted();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to delete phone number");
    }
  }

  async function placeCall() {
    const to = callTo.trim();
    if (!isE164(to)) {
      setCallError("Enter a valid E.164 number, e.g. +14155550123");
      return;
    }
    setCalling(true);
    setCallError(null);
    setCallResult(null);
    try {
      const call = await api.createPhoneCall({
        from_number: phone.phone_number,
        to_number: to,
      });
      setCallResult(call.call_id);
    } catch (e: unknown) {
      setCallError(e instanceof Error ? e.message : "Failed to place call");
    } finally {
      setCalling(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-8 py-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          {renaming ? (
            <TextInput
              value={nick}
              onChange={(e) => setNick(e.target.value)}
              onBlur={saveNickname}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveNickname();
                if (e.key === "Escape") {
                  setNick(phone.nickname ?? "");
                  setRenaming(false);
                }
              }}
              placeholder="Nickname"
              autoFocus
              className="max-w-xs"
            />
          ) : (
            <h1 className="flex items-center gap-2 text-[17px] font-semibold">
              {phone.nickname ?? phone.phone_number}
              <button
                onClick={() => setRenaming(true)}
                className="rounded p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
                aria-label="Rename"
              >
                <Pencil className="size-3.5" />
              </button>
            </h1>
          )}
          <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-sub">
            <CopyId value={phone.phone_number} display={`ID: ${phone.phone_number}`} />
            <span aria-hidden>·</span>
            <span>Provider: {phone.provider}</span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            onClick={() => {
              setCallTo("");
              setCallResult(null);
              setCallError(null);
              setCallOpen(true);
            }}
          >
            <PhoneOutgoing className="size-3.5" />
            Make an outbound call
          </Button>
          <Button variant="danger" onClick={remove} aria-label="Delete phone number">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>

      {error && (
        <p className="mt-3 rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-[13px] text-bad">
          {error}
        </p>
      )}

      <div className="mt-6 space-y-6">
        <AgentCard
          title="Inbound Call Agent"
          agents={agents}
          selectedAgentId={phone.inbound_agent_id}
          versionTag={phone.inbound_agent_version_tag ?? "Latest Published"}
          onSelectAgent={(id) => update({ inbound_agent_id: id })}
        >
          <WebhookCheckbox
            url={phone.inbound_webhook_enabled ? phone.inbound_webhook_url : undefined}
            onSave={(url) => update({ inbound_webhook_url: url })}
          />
          <Field label="Allowed Inbound Countries">
            <CountryTags countries={phone.allowed_inbound_countries} />
          </Field>
          <Field
            label="Fallback Number"
            hint="When inbound call concurrency is reached and cannot free up after extended ringing, will fallback to this number."
          >
            <TextInput
              placeholder="+11234567890"
              value={fallback}
              onChange={(e) => {
                setFallback(e.target.value);
                setFallbackError(null);
              }}
              onBlur={saveFallback}
              onKeyDown={(e) => e.key === "Enter" && saveFallback()}
            />
            {fallbackError && <p className="mt-1.5 text-[12.5px] text-bad">{fallbackError}</p>}
            {fallbackSaved && !fallbackError && (
              <p className="mt-1.5 text-[12.5px] text-sub">Saved</p>
            )}
          </Field>
        </AgentCard>

        <AgentCard
          title="Outbound Call Agent"
          agents={agents}
          selectedAgentId={phone.outbound_agent_id}
          versionTag={phone.outbound_agent_version_tag ?? "Latest Created"}
          onSelectAgent={(id) => update({ outbound_agent_id: id })}
        >
          <Field label="Allowed Outbound Countries">
            <CountryTags countries={phone.allowed_outbound_countries} />
          </Field>
        </AgentCard>

        <section>
          <h2 className="mb-2 text-[14px] font-semibold">Advanced Add-Ons</h2>
          <div className="space-y-3">
            {ADD_ONS.map((a) => (
              <div
                key={a.name}
                className="flex items-center justify-between gap-4 rounded-xl border border-line bg-white p-4 shadow-sm"
              >
                <div>
                  <div className="text-[13.5px] font-semibold">{a.name}</div>
                  <p className="mt-0.5 text-[12.5px] text-sub">{a.desc}</p>
                </div>
                <button
                  disabled
                  title="Not available yet"
                  className="inline-flex shrink-0 items-center gap-0.5 text-[13px] font-medium text-faint cursor-not-allowed"
                >
                  {a.action} <ChevronRight className="size-3.5" />
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>

      <Modal
        open={callOpen}
        onClose={() => setCallOpen(false)}
        title="Make an outbound call"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCallOpen(false)}>
              Close
            </Button>
            <Button variant="primary" disabled={!callTo.trim() || calling} onClick={placeCall}>
              {calling ? "Calling…" : "Call"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="From">
            <TextInput value={phone.phone_number} disabled />
          </Field>
          <Field label="To" hint="E.164 format, e.g. +14155550123">
            <TextInput
              value={callTo}
              onChange={(e) => setCallTo(e.target.value)}
              placeholder="+14155550123"
              autoFocus
            />
          </Field>
          {callError && <p className="text-[13px] text-bad">{callError}</p>}
          {callResult && (
            <p className="rounded-lg border border-green-100 bg-green-50 px-3 py-2 text-[13px] text-green-700">
              Call started — ID: <span className="font-medium">{callResult}</span>
            </p>
          )}
        </div>
      </Modal>
    </div>
  );
}
