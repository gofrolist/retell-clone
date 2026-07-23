"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import Select from "@/components/ui/Select";
import { api } from "@/lib/api";
import type { ContactFieldDefinition } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  Calendar,
  Eye,
  EyeOff,
  GripVertical,
  Hash,
  Link2,
  Plus,
  Settings,
  Trash2,
  Type,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

export type ContactColumnKey =
  | "phone_number"
  | "first_name"
  | "last_name"
  | "timezone"
  | "contact_id"
  | "related_conversations"
  | "latest_conversation"
  | "do_not_call"
  | "external_id";

type ColumnType = "text" | "number" | "date" | "boolean";

export const CONTACT_COLUMNS: Record<ContactColumnKey, { label: string; type: ColumnType }> = {
  phone_number: { label: "Phone Number", type: "text" },
  first_name: { label: "First Name", type: "text" },
  last_name: { label: "Last Name", type: "text" },
  timezone: { label: "Timezone", type: "text" },
  contact_id: { label: "Contact ID", type: "text" },
  related_conversations: { label: "Related Conversations", type: "number" },
  latest_conversation: { label: "Latest Conversation", type: "date" },
  do_not_call: { label: "Do Not Call", type: "boolean" },
  external_id: { label: "External ID", type: "text" },
};

/** Custom contact-field columns are keyed "custom:<field key>". */
export const CUSTOM_COLUMN_PREFIX = "custom:";

export function customColumnKey(fieldKey: string): string {
  return `${CUSTOM_COLUMN_PREFIX}${fieldKey}`;
}

export function customFieldKeyOf(columnKey: string): string | null {
  return columnKey.startsWith(CUSTOM_COLUMN_PREFIX)
    ? columnKey.slice(CUSTOM_COLUMN_PREFIX.length)
    : null;
}

const FIELD_TYPE_TO_COLUMN: Record<ContactFieldDefinition["type"], ColumnType> = {
  string: "text",
  number: "number",
  boolean: "boolean",
  date: "date",
};

/** Label/type for any column key, built-in or custom. */
export function columnMeta(
  key: string,
  fieldDefs: ContactFieldDefinition[],
): { label: string; type: ColumnType } {
  const fieldKey = customFieldKeyOf(key);
  if (fieldKey !== null) {
    const def = fieldDefs.find((d) => d.key === fieldKey);
    return { label: def?.label ?? fieldKey, type: FIELD_TYPE_TO_COLUMN[def?.type ?? "string"] };
  }
  return CONTACT_COLUMNS[key as ContactColumnKey] ?? { label: key, type: "text" };
}

export interface ColumnConfig {
  key: string; // ContactColumnKey | "custom:<field key>"
  visible: boolean;
}

// Retell's default column set; timezone is our extra and starts hidden.
export const DEFAULT_COLUMNS: ColumnConfig[] = [
  { key: "phone_number", visible: true },
  { key: "first_name", visible: true },
  { key: "last_name", visible: true },
  { key: "contact_id", visible: true },
  { key: "related_conversations", visible: true },
  { key: "latest_conversation", visible: true },
  { key: "do_not_call", visible: true },
  { key: "external_id", visible: true },
  { key: "timezone", visible: false },
];

const COLUMNS_STORAGE_KEY = "arhiteq.contacts.columns.v1";

/** Reconcile a column list with the known built-ins + the workspace's custom
 * fields: drop unknown keys, append newly-appeared ones. */
export function reconcileColumns(
  stored: ColumnConfig[],
  fieldDefs: ContactFieldDefinition[],
): ColumnConfig[] {
  const known = stored.filter((c) => {
    const fieldKey = customFieldKeyOf(c.key);
    if (fieldKey !== null) return fieldDefs.some((d) => d.key === fieldKey);
    return c.key in CONTACT_COLUMNS;
  });
  const missingBuiltin = DEFAULT_COLUMNS.filter((d) => !known.some((c) => c.key === d.key));
  const missingCustom = fieldDefs
    .filter((d) => !known.some((c) => c.key === customColumnKey(d.key)))
    .map((d) => ({ key: customColumnKey(d.key), visible: true }));
  return [...known, ...missingBuiltin, ...missingCustom];
}

/** Stored config reconciled with the known column set (drops unknown, appends new). */
export function loadColumnConfig(fieldDefs: ContactFieldDefinition[] = []): ColumnConfig[] {
  try {
    const raw = localStorage.getItem(COLUMNS_STORAGE_KEY);
    if (!raw) return reconcileColumns(DEFAULT_COLUMNS, fieldDefs);
    return reconcileColumns(JSON.parse(raw) as ColumnConfig[], fieldDefs);
  } catch {
    return reconcileColumns(DEFAULT_COLUMNS, fieldDefs);
  }
}

export function saveColumnConfig(config: ColumnConfig[]): void {
  try {
    localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(config));
  } catch {
    // storage full/blocked — column prefs just won't persist
  }
}

const TYPE_ICONS: Record<ColumnType, typeof Type> = {
  text: Type,
  number: Hash,
  date: Calendar,
  boolean: Link2,
};

const FIELD_TYPE_OPTIONS = [
  { value: "string", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Boolean" },
  { value: "date", label: "Date" },
];

function suggestKey(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/^[0-9]/, "f$&")
    .slice(0, 64);
}

function ManageFieldsModal({
  open,
  onClose,
  fieldDefs,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  fieldDefs: ContactFieldDefinition[];
  onSaved: (defs: ContactFieldDefinition[]) => void;
}) {
  const [draft, setDraft] = useState<ContactFieldDefinition[]>(fieldDefs);
  const [newLabel, setNewLabel] = useState("");
  const [newKey, setNewKey] = useState("");
  const [keyTouched, setKeyTouched] = useState(false);
  const [newType, setNewType] = useState<ContactFieldDefinition["type"]>("string");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Re-seed the draft each time the modal opens.
  useEffect(() => {
    if (!open) return;
    setDraft(fieldDefs);
    setNewLabel("");
    setNewKey("");
    setKeyTouched(false);
    setNewType("string");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- seed on open only
  }, [open]);

  const addField = () => {
    const label = newLabel.trim();
    const key = (keyTouched ? newKey : suggestKey(newLabel)).trim();
    if (!label || !key) return;
    if (draft.some((d) => d.key === key)) {
      setError(`A field with key "${key}" already exists`);
      return;
    }
    setDraft((cur) => [...cur, { key, label, type: newType }]);
    setNewLabel("");
    setNewKey("");
    setKeyTouched(false);
    setNewType("string");
    setError(null);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const ws = await api.updateWorkspace({
        settings: { contact_field_definitions: draft },
      });
      onSaved(ws.settings.contact_field_definitions);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save contact fields");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Manage contact fields"
      width="max-w-lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={save} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <p className="text-[12.5px] text-sub">
          Custom fields appear as table columns and on every contact. Deleting a field hides it
          — values already stored on contacts are kept.
        </p>

        {draft.length > 0 && (
          <div className="divide-y divide-line rounded-lg border border-line">
            {draft.map((d) => {
              const TypeIcon = TYPE_ICONS[FIELD_TYPE_TO_COLUMN[d.type]];
              return (
                <div key={d.key} className="flex items-center gap-2 px-3 py-2">
                  <TypeIcon className="size-3.5 shrink-0 text-sub" />
                  <TextInput
                    value={d.label}
                    onChange={(e) =>
                      setDraft((cur) =>
                        cur.map((x) => (x.key === d.key ? { ...x, label: e.target.value } : x)),
                      )
                    }
                    className="h-8 max-w-48"
                  />
                  <span className="grow truncate font-mono text-[12px] text-faint">{d.key}</span>
                  <button
                    onClick={() => setDraft((cur) => cur.filter((x) => x.key !== d.key))}
                    className="rounded-md p-1 text-sub hover:bg-app hover:text-bad cursor-pointer"
                    aria-label={`Delete ${d.label}`}
                  >
                    <Trash2 className="size-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}

        <div className="rounded-lg border border-line bg-app/50 p-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Label">
              <TextInput
                placeholder="e.g. Plan"
                value={newLabel}
                onChange={(e) => setNewLabel(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addField()}
              />
            </Field>
            <Field label="Type">
              <Select
                value={newType}
                onChange={(v) => setNewType(v as ContactFieldDefinition["type"])}
                options={FIELD_TYPE_OPTIONS}
                className="w-full"
              />
            </Field>
          </div>
          <Field label="Key" hint="snake_case; becomes the dynamic-variable name." className="mt-3">
            <TextInput
              placeholder={suggestKey(newLabel) || "field_key"}
              value={keyTouched ? newKey : suggestKey(newLabel)}
              onChange={(e) => {
                setKeyTouched(true);
                setNewKey(e.target.value);
              }}
              className="font-mono"
            />
          </Field>
          <div className="mt-3 flex justify-end">
            <Button size="sm" onClick={addField} disabled={!newLabel.trim()}>
              <Plus className="size-3.5" /> Add field
            </Button>
          </div>
        </div>

        {error && <p className="text-[12.5px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}

export default function ManageTablePanel({
  columns,
  fieldDefs,
  onFieldDefsChange,
  onApply,
  onClose,
}: {
  columns: ColumnConfig[];
  fieldDefs: ContactFieldDefinition[];
  onFieldDefsChange: (defs: ContactFieldDefinition[]) => void;
  onApply: (columns: ColumnConfig[]) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(columns);
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [fieldsOpen, setFieldsOpen] = useState(false);

  const moveTo = (target: number) => {
    if (dragIndex === null || dragIndex === target) return;
    setDraft((cur) => {
      const next = [...cur];
      const [item] = next.splice(dragIndex, 1);
      next.splice(target, 0, item);
      return next;
    });
    setDragIndex(target);
  };

  return (
    <div className="fixed inset-0 z-40" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="absolute inset-y-0 right-0 flex w-[380px] flex-col border-l border-line bg-card shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4">
          <h2 className="text-[15px] font-semibold">Manage table</h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-sub hover:bg-app cursor-pointer"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="min-h-0 grow overflow-y-auto px-5">
          <Button className="w-full" onClick={() => setFieldsOpen(true)}>
            <Settings className="size-3.5" />
            Manage contact fields
          </Button>

          <div className="mt-3">
            {draft.map((col, i) => {
              const meta = columnMeta(col.key, fieldDefs);
              const TypeIcon = TYPE_ICONS[meta.type];
              return (
                <div
                  key={col.key}
                  draggable
                  onDragStart={(e) => {
                    setDragIndex(i);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    moveTo(i);
                  }}
                  onDragEnd={() => setDragIndex(null)}
                  className={cn(
                    "flex items-center gap-2 rounded-lg px-1.5 py-2 hover:bg-app/70",
                    dragIndex === i && "bg-app",
                    !col.visible && "opacity-50",
                  )}
                >
                  <GripVertical className="size-4 shrink-0 cursor-grab text-faint" />
                  <TypeIcon className="size-3.5 shrink-0 text-sub" />
                  <span className="grow truncate text-[13px]">
                    {meta.label}
                    {customFieldKeyOf(col.key) !== null && (
                      <span className="ml-1.5 text-[11px] text-faint">custom</span>
                    )}
                  </span>
                  <button
                    onClick={() =>
                      setDraft((cur) =>
                        cur.map((c) => (c.key === col.key ? { ...c, visible: !c.visible } : c)),
                      )
                    }
                    className="rounded-md p-1 text-sub hover:bg-black/5 cursor-pointer"
                    aria-label={col.visible ? `Hide ${meta.label}` : `Show ${meta.label}`}
                  >
                    {col.visible ? <Eye className="size-4" /> : <EyeOff className="size-4" />}
                  </button>
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-line px-5 py-3.5">
          <Button onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={draft.every((c) => !c.visible)}
            onClick={() => onApply(draft)}
          >
            Apply
          </Button>
        </div>
      </div>

      <ManageFieldsModal
        open={fieldsOpen}
        onClose={() => setFieldsOpen(false)}
        fieldDefs={fieldDefs}
        onSaved={(defs) => {
          onFieldDefsChange(defs);
          setDraft((cur) => reconcileColumns(cur, defs));
        }}
      />
    </div>
  );
}
