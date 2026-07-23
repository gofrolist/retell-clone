"use client";

import ContactDrawer, { TIMEZONE_OPTIONS } from "@/components/contacts/ContactDrawer";
import CustomFieldInputs, {
  formatCustomValue,
  type CustomFieldValues,
} from "@/components/contacts/CustomFieldInputs";
import ManageTablePanel, {
  DEFAULT_COLUMNS,
  columnMeta,
  customFieldKeyOf,
  loadColumnConfig,
  reconcileColumns,
  saveColumnConfig,
  type ColumnConfig,
  type ContactColumnKey,
} from "@/components/contacts/ManageTablePanel";
import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import LoadError from "@/components/ui/LoadError";
import Modal from "@/components/ui/Modal";
import RowMenu from "@/components/ui/RowMenu";
import Select from "@/components/ui/Select";
import SearchInput from "@/components/ui/SearchInput";
import Toggle from "@/components/ui/Toggle";
import Tooltip from "@/components/ui/Tooltip";
import { api } from "@/lib/api";
import type { Contact, ContactFieldDefinition } from "@/lib/types";
import { useApiData } from "@/lib/useApiData";
import { cn, formatDateTimeZone } from "@/lib/utils";
import {
  ChevronDown,
  Contact as ContactIcon,
  Database,
  Info,
  ListFilter,
  Plug2,
  Plus,
  RefreshCw,
  Settings,
  SlidersHorizontal,
  UserPlus,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

// ------------------------------------------------------------------ filters

type FilterField =
  | "phone_number"
  | "contact_id"
  | "external_id"
  | "do_not_call"
  | "latest_conversation";

const FILTER_FIELDS: { field: FilterField; label: string }[] = [
  { field: "phone_number", label: "Phone Number" },
  { field: "contact_id", label: "Contact ID" },
  { field: "external_id", label: "External ID" },
  { field: "do_not_call", label: "Do Not Call" },
  { field: "latest_conversation", label: "Latest Conversation" },
];

interface ContactFilter {
  field: FilterField;
  value: string; // text query | "yes"/"no" | from-date (yyyy-mm-dd)
  value2?: string; // latest_conversation only: to-date
}

function matchesFilter(c: Contact, f: ContactFilter): boolean {
  switch (f.field) {
    case "phone_number":
      return c.phone_number.toLowerCase().includes(f.value.toLowerCase());
    case "contact_id":
      return c.contact_id.toLowerCase().includes(f.value.toLowerCase());
    case "external_id":
      return (c.external_id ?? "").toLowerCase().includes(f.value.toLowerCase());
    case "do_not_call":
      return c.do_not_call === (f.value === "yes");
    case "latest_conversation": {
      if (!c.latest_conversation) return false;
      if (f.value && c.latest_conversation < new Date(`${f.value}T00:00:00`).getTime())
        return false;
      if (f.value2 && c.latest_conversation > new Date(`${f.value2}T23:59:59.999`).getTime())
        return false;
      return true;
    }
  }
}

function filterChipLabel(f: ContactFilter): string {
  const label = FILTER_FIELDS.find((x) => x.field === f.field)?.label ?? f.field;
  if (f.field === "do_not_call") return `${label}: ${f.value === "yes" ? "Yes" : "No"}`;
  if (f.field === "latest_conversation")
    return `${label}: ${f.value || "…"} – ${f.value2 || "…"}`;
  return `${label}: ${f.value}`;
}

function FilterMenu({
  onApply,
  onClose,
}: {
  onApply: (f: ContactFilter) => void;
  onClose: () => void;
}) {
  const [field, setField] = useState<FilterField | null>(null);
  const [value, setValue] = useState("");
  const [value2, setValue2] = useState("");

  const apply = () => {
    if (!field) return;
    onApply({ field, value, ...(field === "latest_conversation" ? { value2 } : {}) });
    onClose();
  };

  const dateInput = (v: string, set: (v: string) => void, label: string) => (
    <label className="block text-[13px]">
      <span className="mb-1 block font-medium">{label}</span>
      <input
        type="date"
        value={v}
        onChange={(e) => set(e.target.value)}
        className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none focus:border-accent"
      />
    </label>
  );

  return (
    <>
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className="absolute left-0 top-full z-30 mt-1 w-72 rounded-xl border border-line bg-white p-1.5 shadow-lg">
        {field === null ? (
          FILTER_FIELDS.map((f) => (
            <button
              key={f.field}
              onClick={() => {
                setField(f.field);
                if (f.field === "do_not_call") setValue("yes");
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-app cursor-pointer"
            >
              <Plus className="size-3.5 text-sub" />
              {f.label}
            </button>
          ))
        ) : (
          <div className="space-y-2.5 p-1.5">
            <div className="text-[13px] font-semibold">
              {FILTER_FIELDS.find((x) => x.field === field)?.label}
            </div>
            {field === "do_not_call" ? (
              <Select
                value={value}
                onChange={setValue}
                options={[
                  { value: "yes", label: "Yes" },
                  { value: "no", label: "No" },
                ]}
                className="w-full"
              />
            ) : field === "latest_conversation" ? (
              <>
                {dateInput(value, setValue, "From")}
                {dateInput(value2, setValue2, "To")}
              </>
            ) : (
              <TextInput
                autoFocus
                placeholder="Contains…"
                value={value}
                onChange={(e) => setValue(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && value.trim() && apply()}
              />
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button size="sm" variant="ghost" onClick={onClose}>
                Cancel
              </Button>
              <Button
                size="sm"
                variant="primary"
                disabled={
                  field === "latest_conversation" ? !value && !value2 : !value.trim()
                }
                onClick={apply}
              >
                Apply
              </Button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

// -------------------------------------------------------------- add contact

function AddContactModal({
  open,
  onClose,
  onCreated,
  fieldDefs,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
  fieldDefs: ContactFieldDefinition[];
}) {
  const [phoneNumber, setPhoneNumber] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [timezone, setTimezone] = useState("");
  const [externalId, setExternalId] = useState("");
  const [customValues, setCustomValues] = useState<CustomFieldValues>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setPhoneNumber("");
    setFirstName("");
    setLastName("");
    setTimezone("");
    setExternalId("");
    setCustomValues({});
    setError(null);
    onClose();
  };

  const create = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const filled = Object.fromEntries(
        Object.entries(customValues).filter(([, v]) => v !== null && v !== ""),
      );
      await api.createContact({
        phone_number: phoneNumber.trim(),
        first_name: firstName.trim(),
        last_name: lastName.trim(),
        ...(timezone ? { timezone } : {}),
        ...(externalId.trim() ? { external_id: externalId.trim() } : {}),
        ...(Object.keys(filled).length ? { custom_fields: filled } : {}),
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
        <Field label="Timezone" hint="Used to answer time questions and time the calls right.">
          <Select value={timezone} onChange={setTimezone} options={TIMEZONE_OPTIONS} className="w-full" />
        </Field>
        <Field label="External ID" hint="Optional — your CRM or system identifier.">
          <TextInput value={externalId} onChange={(e) => setExternalId(e.target.value)} />
        </Field>
        <CustomFieldInputs defs={fieldDefs} values={customValues} onChange={setCustomValues} />
        {error && <p className="text-[12.5px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}

// -------------------------------------------------------------------- cells

function cellContent(
  c: Contact,
  key: string,
  onDoNotCall: (id: string, v: boolean) => void,
  fieldDefs: ContactFieldDefinition[],
): React.ReactNode {
  const fieldKey = customFieldKeyOf(key);
  if (fieldKey !== null) {
    const def = fieldDefs.find((d) => d.key === fieldKey);
    const text = formatCustomValue(def, c.custom_fields?.[fieldKey]);
    return text === "—" ? <span className="text-sub">—</span> : text;
  }
  switch (key as ContactColumnKey) {
    case "phone_number":
      return <span className="tabular-nums">{c.phone_number}</span>;
    case "first_name":
      return c.first_name || "—";
    case "last_name":
      return c.last_name || "—";
    case "timezone":
      return c.timezone || "—";
    case "contact_id":
      return <span className="font-mono text-[12.5px] text-sub">{c.contact_id}</span>;
    case "related_conversations":
      return <span className="tabular-nums">{c.related_conversations}</span>;
    case "latest_conversation":
      return c.latest_conversation ? (
        formatDateTimeZone(c.latest_conversation)
      ) : (
        <span className="text-sub">—</span>
      );
    case "do_not_call":
      // One-click compliance action — must not require opening the drawer.
      // stopPropagation keeps the row click (open drawer) out of the toggle.
      return (
        <span onClick={(e) => e.stopPropagation()} className="inline-flex">
          <Toggle checked={c.do_not_call} onChange={(v) => onDoNotCall(c.contact_id, v)} />
        </span>
      );
    case "external_id":
      return c.external_id || <span className="text-sub">—</span>;
  }
}

/** Keeps the drawer deep-linkable (?contact=…) without a Suspense-gated hook. */
function setContactParam(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("contact", id);
  else url.searchParams.delete("contact");
  window.history.replaceState(null, "", url);
}

// --------------------------------------------------------------------- page

export default function ContactsPage() {
  const { data, setData: setContacts, loading, error, setError, reload } = useApiData(
    () => api.listContacts(),
  );
  const contacts = useMemo(() => data ?? [], [data]);
  const [bannerOpen, setBannerOpen] = useState(true);
  const [addOpen, setAddOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [filters, setFilters] = useState<ContactFilter[]>([]);
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Column prefs load after mount so SSR markup matches first client render;
  // custom contact-field columns join once workspace settings arrive.
  const [columns, setColumns] = useState<ColumnConfig[]>(DEFAULT_COLUMNS);
  const [fieldDefs, setFieldDefs] = useState<ContactFieldDefinition[]>([]);
  useEffect(() => {
    setColumns(loadColumnConfig());
    api
      .getWorkspace()
      .then((ws) => {
        const defs = ws.settings.contact_field_definitions ?? [];
        setFieldDefs(defs);
        setColumns((cur) => reconcileColumns(cur, defs));
      })
      .catch(() => {}); // backend banner covers unreachable
  }, []);
  const visibleColumns = columns.filter((c) => c.visible);

  const applyFieldDefs = (defs: ContactFieldDefinition[]) => {
    setFieldDefs(defs);
    setColumns((cur) => {
      const next = reconcileColumns(cur, defs);
      saveColumnConfig(next);
      return next;
    });
  };

  // Deep link: open the drawer for ?contact=… once contacts arrive.
  const openedFromUrl = useRef(false);
  useEffect(() => {
    if (openedFromUrl.current || !data) return;
    openedFromUrl.current = true;
    const id = new URLSearchParams(window.location.search).get("contact");
    if (id && data.some((c) => c.contact_id === id)) setSelectedId(id);
  }, [data]);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    return contacts
      .filter((c) => filters.every((f) => matchesFilter(c, f)))
      .filter(
        (c) =>
          !q ||
          [c.phone_number, c.first_name, c.last_name, c.contact_id, c.external_id ?? ""].some(
            (v) => v.toLowerCase().includes(q),
          ),
      )
      .sort((a, b) => (b.latest_conversation ?? 0) - (a.latest_conversation ?? 0));
  }, [contacts, filters, search]);

  const selected = selectedId
    ? contacts.find((c) => c.contact_id === selectedId) ?? null
    : null;

  const openContact = (id: string | null) => {
    setSelectedId(id);
    setContactParam(id);
  };

  const navigate = (dir: 1 | -1) => {
    if (!selectedId) return;
    const idx = rows.findIndex((c) => c.contact_id === selectedId);
    const next = rows[idx + dir];
    if (next) openContact(next.contact_id);
  };

  const deleteContact = async (id: string) => {
    try {
      await api.deleteContact(id);
      setContacts((cur) => (cur ?? []).filter((c) => c.contact_id !== id));
      if (selectedId === id) openContact(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete contact");
    }
  };

  // Optimistic per-row DNC flip; revert on failure.
  const setDoNotCall = async (id: string, v: boolean) => {
    const prev = data;
    setContacts((cur) =>
      (cur ?? []).map((c) => (c.contact_id === id ? { ...c, do_not_call: v } : c)),
    );
    try {
      await api.updateContact(id, { do_not_call: v });
    } catch {
      setContacts(prev);
    }
  };

  const actionItem = (
    icon: React.ReactNode,
    label: string,
    onClick?: () => void,
    disabled = false,
  ) => (
    <button
      key={label}
      disabled={disabled}
      title={disabled ? "Not available yet" : undefined}
      onClick={() => {
        setActionsOpen(false);
        onClick?.();
      }}
      className={cn(
        "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[13px]",
        disabled ? "cursor-not-allowed text-faint" : "hover:bg-app cursor-pointer",
      )}
    >
      {icon}
      {label}
    </button>
  );

  return (
    <div className="flex h-full flex-col px-6 pt-5">
      <div className="mb-4 flex items-center gap-2">
        <ContactIcon className="size-4.5 text-sub" strokeWidth={1.8} />
        <h1 className="text-[17px] font-semibold">Contacts</h1>
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

      <div className="mb-3 flex items-center gap-2">
        <div className="relative">
          <Button onClick={() => setFilterOpen((v) => !v)}>
            <ListFilter className="size-3.5" />
            Filter
          </Button>
          {filterOpen && (
            <FilterMenu
              onApply={(f) =>
                setFilters((cur) => [...cur.filter((x) => x.field !== f.field), f])
              }
              onClose={() => setFilterOpen(false)}
            />
          )}
        </div>

        {filters.map((f) => (
          <span
            key={f.field}
            className="flex items-center gap-1.5 rounded-lg border border-line bg-app px-2.5 py-1.5 text-[12.5px]"
          >
            {filterChipLabel(f)}
            <button
              onClick={() => setFilters((cur) => cur.filter((x) => x.field !== f.field))}
              className="rounded p-0.5 text-faint hover:bg-black/5 hover:text-ink cursor-pointer"
              aria-label={`Remove ${filterChipLabel(f)} filter`}
            >
              <X className="size-3" />
            </button>
          </span>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <SearchInput value={search} onChange={setSearch} placeholder="Search" className="w-64" />
          <Button
            variant="ghost"
            aria-label="Manage table"
            onClick={() => setManageOpen(true)}
          >
            <SlidersHorizontal className="size-4" />
          </Button>
          <div className="relative">
            <Button variant="primary" onClick={() => setActionsOpen((v) => !v)}>
              Actions
              <ChevronDown className="size-3.5" />
            </Button>
            {actionsOpen && (
              <>
                <div className="fixed inset-0 z-20" onClick={() => setActionsOpen(false)} />
                <div className="absolute right-0 top-full z-30 mt-1 w-64 rounded-xl border border-line bg-white p-1.5 shadow-lg">
                  {actionItem(<Settings className="size-4" />, "Manage contact fields", () =>
                    setManageOpen(true),
                  )}
                  {actionItem(<Plug2 className="size-4" />, "Manage CRM sync", undefined, true)}
                  {actionItem(<RefreshCw className="size-4" />, "Run full sync", undefined, true)}
                  {actionItem(
                    <Database className="size-4" />,
                    "Backfill from Post-Call Data",
                    undefined,
                    true,
                  )}
                  {actionItem(<UserPlus className="size-4" />, "Add contact", () => setAddOpen(true))}
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="min-h-0 grow overflow-auto rounded-t-lg border border-line border-b-0">
        <table className="w-full min-w-[880px] text-left">
          <thead className="sticky top-0 z-[1] bg-card">
            <tr className="border-b border-line text-[13px] text-sub">
              {visibleColumns.map((col) => (
                <th key={col.key} className="whitespace-nowrap px-3 py-2.5 font-medium first:pl-4">
                  <span className="inline-flex items-center gap-1">
                    {columnMeta(col.key, fieldDefs).label}
                    {col.key === "do_not_call" && (
                      <Tooltip label="Contacts marked Do Not Call are skipped by batch calls.">
                        <Info className="size-3.5 text-faint" />
                      </Tooltip>
                    )}
                  </span>
                </th>
              ))}
              <th className="w-10 px-3 py-2.5" />
            </tr>
          </thead>
          <tbody>
            {loading && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-4 py-10 text-center text-[13px] text-sub">
                  Loading contacts…
                </td>
              </tr>
            )}
            {!loading && error && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-4 py-10 text-center text-[13px]">
                  <LoadError error={error} onRetry={reload} />
                </td>
              </tr>
            )}
            {!loading && !error && rows.length === 0 && (
              <tr>
                <td colSpan={visibleColumns.length + 1} className="px-4 py-10 text-center text-[13px] text-sub">
                  {contacts.length === 0
                    ? "No contacts yet. Add one to enrich call data with names."
                    : "No contacts match the current filters."}
                </td>
              </tr>
            )}
            {!loading &&
              !error &&
              rows.map((c) => (
                <tr
                  key={c.contact_id}
                  onClick={() => openContact(c.contact_id)}
                  className={cn(
                    "cursor-pointer border-b border-line/70 hover:bg-app/60",
                    selectedId === c.contact_id && "bg-app/60",
                  )}
                >
                  {visibleColumns.map((col) => (
                    <td key={col.key} className="whitespace-nowrap px-3 py-3 text-[13px] first:pl-4">
                      {cellContent(c, col.key, setDoNotCall, fieldDefs)}
                    </td>
                  ))}
                  <td className="px-3 py-3" onClick={(e) => e.stopPropagation()}>
                    <RowMenu onDelete={() => deleteContact(c.contact_id)} />
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <AddContactModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onCreated={reload}
        fieldDefs={fieldDefs}
      />

      {manageOpen && (
        <ManageTablePanel
          columns={columns}
          fieldDefs={fieldDefs}
          onFieldDefsChange={applyFieldDefs}
          onClose={() => setManageOpen(false)}
          onApply={(next) => {
            setColumns(next);
            saveColumnConfig(next);
            setManageOpen(false);
          }}
        />
      )}

      {selected && (
        <ContactDrawer
          contact={selected}
          fieldDefs={fieldDefs}
          onClose={() => openContact(null)}
          onNavigate={navigate}
          onUpdated={(updated) =>
            setContacts((cur) =>
              (cur ?? []).map((c) => (c.contact_id === updated.contact_id ? updated : c)),
            )
          }
        />
      )}
    </div>
  );
}
