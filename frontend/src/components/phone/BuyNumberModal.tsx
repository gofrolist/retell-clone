"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { api, ApiError } from "@/lib/api";
import type { Agent } from "@/lib/types";
import { isE164 } from "@/lib/utils";
import { Info } from "lucide-react";
import { useState } from "react";

/**
 * Number purchase has no backend API yet — this is an honest "connect a
 * number you already own" flow backed by POST /create-phone-number.
 */
export default function BuyNumberModal({
  open,
  onClose,
  agents,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  agents: Agent[];
  onCreated: (phoneNumber: string) => void;
}) {
  const [number, setNumber] = useState("");
  const [nickname, setNickname] = useState("");
  const [inboundAgent, setInboundAgent] = useState("");
  const [outboundAgent, setOutboundAgent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const agentOptions = [
    { value: "", label: "No agent" },
    ...agents.map((a) => ({ value: a.agent_id, label: a.agent_name })),
  ];

  function reset() {
    setNumber("");
    setNickname("");
    setInboundAgent("");
    setOutboundAgent("");
    setError(null);
  }

  async function submit() {
    const e164 = number.trim();
    if (!isE164(e164)) {
      setError("Enter a valid E.164 number, e.g. +14155550123");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await api.createPhoneNumber({
        phone_number: e164,
        ...(nickname.trim() ? { nickname: nickname.trim() } : {}),
        ...(inboundAgent ? { inbound_agent_id: inboundAgent } : {}),
        ...(outboundAgent ? { outbound_agent_id: outboundAgent } : {}),
      });
      reset();
      onCreated(created.phone_number);
    } catch (e: unknown) {
      setError(
        e instanceof ApiError && e.status === 409
          ? "This phone number is already connected."
          : e instanceof Error
            ? e.message
            : "Failed to connect number",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => {
        reset();
        onClose();
      }}
      title="Connect Phone Number"
      width="max-w-xl"
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              reset();
              onClose();
            }}
          >
            Cancel
          </Button>
          <Button variant="primary" disabled={!number.trim() || submitting} onClick={submit}>
            {submitting ? "Connecting…" : "Connect"}
          </Button>
        </>
      }
    >
      <div className="mb-4 flex items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-[13px] text-accent-deep">
        <Info className="size-4 shrink-0" />
        Buying new numbers isn&apos;t available yet — connect a number you already own.
      </div>

      <div className="space-y-4">
        <Field label="Phone Number" hint="E.164 format, e.g. +14155550123">
          <TextInput
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            placeholder="+14155550123"
            autoFocus
          />
        </Field>

        <Field label="Nickname (optional)">
          <TextInput
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="e.g. Support line"
          />
        </Field>

        <Field label="Inbound Agent (optional)">
          <Select
            value={inboundAgent}
            onChange={setInboundAgent}
            className="w-full"
            options={agentOptions}
          />
        </Field>

        <Field label="Outbound Agent (optional)">
          <Select
            value={outboundAgent}
            onChange={setOutboundAgent}
            className="w-full"
            options={agentOptions}
          />
        </Field>

        {error && <p className="text-[13px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
