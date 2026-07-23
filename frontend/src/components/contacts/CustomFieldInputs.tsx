"use client";

import { Field, TextInput } from "@/components/ui/Field";
import Toggle from "@/components/ui/Toggle";
import type { ContactFieldDefinition } from "@/lib/types";

export type CustomFieldValues = Record<string, string | number | boolean | null>;

/** Renders one input per workspace-defined custom contact field. */
export default function CustomFieldInputs({
  defs,
  values,
  onChange,
}: {
  defs: ContactFieldDefinition[];
  values: CustomFieldValues;
  onChange: (next: CustomFieldValues) => void;
}) {
  if (defs.length === 0) return null;

  const set = (key: string, value: string | number | boolean | null) =>
    onChange({ ...values, [key]: value });

  return (
    <>
      {defs.map((d) => {
        const v = values[d.key];
        if (d.type === "boolean") {
          return (
            <div key={d.key} className="flex items-center justify-between">
              <span className="text-[13px] font-medium">{d.label}</span>
              <Toggle checked={v === true} onChange={(next) => set(d.key, next)} />
            </div>
          );
        }
        if (d.type === "date") {
          return (
            <Field key={d.key} label={d.label}>
              <input
                type="date"
                value={typeof v === "string" ? v : ""}
                onChange={(e) => set(d.key, e.target.value || null)}
                className="h-9 w-full rounded-lg border border-line bg-white px-3 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
              />
            </Field>
          );
        }
        return (
          <Field key={d.key} label={d.label}>
            <TextInput
              inputMode={d.type === "number" ? "decimal" : undefined}
              value={v === null || v === undefined ? "" : String(v)}
              onChange={(e) => {
                const raw = e.target.value;
                if (d.type === "number") {
                  set(d.key, raw === "" ? null : Number.isNaN(Number(raw)) ? raw : Number(raw));
                } else {
                  set(d.key, raw || null);
                }
              }}
            />
          </Field>
        );
      })}
    </>
  );
}

/** Display form of a custom field value ("—" for empty). */
export function formatCustomValue(
  def: ContactFieldDefinition | undefined,
  v: string | number | boolean | null | undefined,
): string {
  if (v === undefined || v === null || v === "") return "—";
  if (def?.type === "boolean" || typeof v === "boolean") return v === true ? "Yes" : "No";
  return String(v);
}
