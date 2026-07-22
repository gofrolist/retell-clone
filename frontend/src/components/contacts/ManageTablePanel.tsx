"use client";

import Button from "@/components/ui/Button";
import { cn } from "@/lib/utils";
import {
  Calendar,
  Eye,
  EyeOff,
  GripVertical,
  Hash,
  Link2,
  Settings,
  Type,
  X,
} from "lucide-react";
import { useState } from "react";

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

export interface ColumnConfig {
  key: ContactColumnKey;
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

/** Stored config reconciled with the known column set (drops unknown, appends new). */
export function loadColumnConfig(): ColumnConfig[] {
  try {
    const raw = localStorage.getItem(COLUMNS_STORAGE_KEY);
    if (!raw) return DEFAULT_COLUMNS;
    const stored = JSON.parse(raw) as ColumnConfig[];
    const known = stored.filter((c) => c.key in CONTACT_COLUMNS);
    const missing = DEFAULT_COLUMNS.filter((d) => !known.some((c) => c.key === d.key));
    return [...known, ...missing];
  } catch {
    return DEFAULT_COLUMNS;
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

export default function ManageTablePanel({
  columns,
  onApply,
  onClose,
}: {
  columns: ColumnConfig[];
  onApply: (columns: ColumnConfig[]) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(columns);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

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
          <Button className="w-full" disabled title="Custom contact fields are not available yet">
            <Settings className="size-3.5" />
            Manage contact fields
          </Button>

          <div className="mt-3">
            {draft.map((col, i) => {
              const meta = CONTACT_COLUMNS[col.key];
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
                  <span className="grow truncate text-[13px]">{meta.label}</span>
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
    </div>
  );
}
