"use client";

import { Field, TextInput } from "@/components/ui/Field";
import { Plus, Trash2 } from "lucide-react";

export type Pair = { key: string; value: string };

/** Explode a `Record<string, unknown>` into editable key/value rows. */
export function toPairs(obj: unknown): Pair[] {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return [];
  return Object.entries(obj as Record<string, unknown>).map(([key, value]) => ({
    key,
    value: String(value ?? ""),
  }));
}

/** Collapse key/value rows into an object; undefined when no row has a key. */
export function fromPairs(pairs: Pair[]): Record<string, string> | undefined {
  const entries = pairs.filter((p) => p.key.trim() !== "");
  if (entries.length === 0) return undefined;
  return Object.fromEntries(entries.map((p) => [p.key.trim(), p.value]));
}

/** Reusable add/edit/delete list of key/value rows (headers, query params, …). */
export function PairRows({
  label,
  addLabel,
  pairs,
  onChange,
  keyPlaceholder = "Key",
  valuePlaceholder = "Value",
}: {
  label?: string;
  addLabel: string;
  pairs: Pair[];
  onChange: (pairs: Pair[]) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
}) {
  const rows = (
    <div className="space-y-1.5">
      {pairs.map((p, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <TextInput
            value={p.key}
            onChange={(e) =>
              onChange(pairs.map((row, idx) => (idx === i ? { ...row, key: e.target.value } : row)))
            }
            placeholder={keyPlaceholder}
          />
          <TextInput
            value={p.value}
            onChange={(e) =>
              onChange(pairs.map((row, idx) => (idx === i ? { ...row, value: e.target.value } : row)))
            }
            placeholder={valuePlaceholder}
          />
          <button
            onClick={() => onChange(pairs.filter((_, idx) => idx !== i))}
            className="rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
            aria-label={`Delete ${(label ?? "key/value").toLowerCase()} row`}
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      ))}
      <button
        onClick={() => onChange([...pairs, { key: "", value: "" }])}
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> {addLabel}
      </button>
    </div>
  );
  return label === undefined ? rows : <Field label={label}>{rows}</Field>;
}
