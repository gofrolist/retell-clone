"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import Toggle from "@/components/ui/Toggle";
import { api } from "@/lib/api";
import type { Contact } from "@/lib/types";
import { cn, formatDate, truncateId } from "@/lib/utils";
import { Contact as ContactIcon, MoreHorizontal, Plug2, Plus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

function RowMenu({ onDelete }: { onDelete: () => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="rounded-md p-1 text-faint hover:bg-app hover:text-ink cursor-pointer"
        aria-label="More"
      >
        <MoreHorizontal className="size-4" />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 w-32 rounded-lg border border-line bg-white p-1 shadow-lg">
          <button
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="flex w-full items-center rounded-md px-2.5 py-1.5 text-left text-[13px] text-bad hover:bg-app cursor-pointer"
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}

function AddContactModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [phoneNumber, setPhoneNumber] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [externalId, setExternalId] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setPhoneNumber("");
    setFirstName("");
    setLastName("");
    setExternalId("");
    setError(null);
    onClose();
  };

  const create = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api.createContact({
        phone_number: phoneNumber.trim(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        ...(externalId.trim() ? { external_id: externalId.trim() } : {}),
      });
      onCreated();
      close();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create contact");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Add Contact"
      width="max-w-md"
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button variant="primary" onClick={create} disabled={submitting || !phoneNumber.trim()}>
            {submitting ? "Adding…" : "Add Contact"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Phone Number">
          <TextInput
            placeholder="+14155550123"
            value={phoneNumber}
            onChange={(e) => setPhoneNumber(e.target.value)}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="First Name">
            <TextInput value={firstName} onChange={(e) => setFirstName(e.target.value)} />
          </Field>
          <Field label="Last Name">
            <TextInput value={lastName} onChange={(e) => setLastName(e.target.value)} />
          </Field>
        </div>
        <Field label="External ID" hint="Optional — your CRM or system identifier.">
          <TextInput value={externalId} onChange={(e) => setExternalId(e.target.value)} />
        </Field>
        {error && <p className="text-[12.5px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}

export default function ContactsPage() {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [bannerOpen, setBannerOpen] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

  const load = () => {
    api
      .listContacts()
      .then((list) => {
        setContacts(list);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load contacts"))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  const setDoNotCall = async (id: string, v: boolean) => {
    const prev = contacts;
    setContacts((cur) => cur.map((c) => (c.contact_id === id ? { ...c, do_not_call: v } : c)));
    try {
      await api.updateContact(id, { do_not_call: v });
    } catch {
      setContacts(prev); // revert optimistic update
    }
  };

  const deleteContact = async (id: string) => {
    try {
      await api.deleteContact(id);
      setContacts((cur) => cur.filter((c) => c.contact_id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete contact");
    }
  };

  return (
    <div className="flex h-full flex-col px-6 pt-5">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ContactIcon className="size-4.5 text-sub" strokeWidth={1.8} />
          <h1 className="text-[17px] font-semibold">Contacts</h1>
        </div>
        <Button variant="primary" onClick={() => setAddOpen(true)}>
          <Plus className="size-3.5" />
          Add Contact
        </Button>
      </div>

      {bannerOpen && (
        <div className="mb-4 flex items-center gap-3 rounded-xl border border-line bg-app px-4 py-3">
          <span className="flex size-9 items-center justify-center rounded-lg border border-line bg-white shadow-sm">
            <Plug2 className="size-4.5 text-sub" strokeWidth={1.8} />
          </span>
          <div className="grow">
            <div className="text-[13.5px] font-semibold">Connect your CRM</div>
            <p className="text-[12.5px] text-sub">
              Sync contacts from HubSpot, Salesforce or GoHighLevel to enrich call data
              automatically.
            </p>
          </div>
          <Button size="sm" variant="primary" disabled title="CRM integrations not available yet">
            Connect
          </Button>
          <button
            onClick={() => setBannerOpen(false)}
            className="rounded-md p-1 text-faint hover:bg-black/5 hover:text-ink cursor-pointer"
            aria-label="Dismiss"
          >
            <X className="size-4" />
          </button>
        </div>
      )}

      <div className="min-h-0 grow overflow-auto rounded-t-lg border border-line border-b-0">
        <table className="w-full min-w-[880px] text-left">
          <thead className="sticky top-0 z-[1] bg-card">
            <tr className="border-b border-line text-[13px] text-sub">
              {[
                "Phone Number",
                "First Name",
                "Last Name",
                "Contact ID",
                "Related Conversations",
                "Latest Conversation",
                "Do Not Call",
                "External ID",
                "",
              ].map((h, i) => (
                <th key={i} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-[13px] text-sub">
                  Loading contacts…
                </td>
              </tr>
            )}
            {!loading && error && (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-[13px]">
                  <span className="text-bad">{error}</span>{" "}
                  <button
                    onClick={() => {
                      setLoading(true);
                      load();
                    }}
                    className="font-medium text-accent-deep hover:underline cursor-pointer"
                  >
                    Retry
                  </button>
                </td>
              </tr>
            )}
            {!loading && !error && contacts.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-10 text-center text-[13px] text-sub">
                  No contacts yet. Add one to enrich call data with names.
                </td>
              </tr>
            )}
            {contacts.map((c) => (
              <tr key={c.contact_id} className="border-b border-line/70 hover:bg-app/60">
                <td className="py-3 pl-4 pr-3 tabular-nums">{c.phone_number}</td>
                <td className="px-3 py-3">{c.first_name}</td>
                <td className="px-3 py-3">{c.last_name}</td>
                <td className="px-3 py-3 font-mono text-[12.5px] text-sub">
                  {truncateId(c.contact_id, 16)}
                </td>
                <td className="px-3 py-3 tabular-nums">{c.related_conversations}</td>
                <td className="px-3 py-3 text-sub">
                  {c.latest_conversation ? formatDate(c.latest_conversation) : "—"}
                </td>
                <td className="px-3 py-3">
                  <span className="flex items-center gap-2">
                    <Toggle
                      checked={c.do_not_call}
                      onChange={(v) => setDoNotCall(c.contact_id, v)}
                    />
                    <span className={cn("text-[12.5px]", c.do_not_call ? "font-medium text-bad" : "text-sub")}>
                      {c.do_not_call ? "Yes" : "No"}
                    </span>
                  </span>
                </td>
                <td className="px-3 py-3 text-sub">{c.external_id ?? "-"}</td>
                <td className="px-3 py-3">
                  <RowMenu onDelete={() => deleteContact(c.contact_id)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <AddContactModal open={addOpen} onClose={() => setAddOpen(false)} onCreated={load} />
    </div>
  );
}
