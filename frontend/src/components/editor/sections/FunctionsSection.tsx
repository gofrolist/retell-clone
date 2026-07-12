"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Toggle from "@/components/ui/Toggle";
import type { RawLlm } from "@/lib/api";
import { cn, isE164 } from "@/lib/utils";
import { useClickOutside } from "@/lib/useClickOutside";
import {
  Pencil,
  PhoneForwarded,
  PhoneOff,
  Plus,
  Trash2,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";

type Tool = NonNullable<RawLlm["general_tools"]>[number];
type ToolKind = "end_call" | "transfer_call" | "custom";

const NAME_RE = /^[a-zA-Z0-9_-]{1,64}$/;
const METHOD_OPTIONS = ["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => ({
  value: m,
  label: m,
}));
const TEXTAREA =
  "w-full resize-none rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15";

type Pair = { key: string; value: string };

function toPairs(obj: unknown): Pair[] {
  if (!obj || typeof obj !== "object" || Array.isArray(obj)) return [];
  return Object.entries(obj as Record<string, unknown>).map(([key, value]) => ({
    key,
    value: String(value ?? ""),
  }));
}

/** Collapse key/value rows into an object; undefined when no row has a key. */
function fromPairs(pairs: Pair[]): Record<string, string> | undefined {
  const entries = pairs.filter((p) => p.key.trim() !== "");
  if (entries.length === 0) return undefined;
  return Object.fromEntries(entries.map((p) => [p.key.trim(), p.value]));
}

function PairRows({
  label,
  addLabel,
  pairs,
  onChange,
}: {
  label: string;
  addLabel: string;
  pairs: Pair[];
  onChange: (pairs: Pair[]) => void;
}) {
  return (
    <Field label={label}>
      <div className="space-y-1.5">
        {pairs.map((p, i) => (
          <div key={i} className="flex items-center gap-1.5">
            <TextInput
              value={p.key}
              onChange={(e) =>
                onChange(pairs.map((row, idx) => (idx === i ? { ...row, key: e.target.value } : row)))
              }
              placeholder="Key"
            />
            <TextInput
              value={p.value}
              onChange={(e) =>
                onChange(pairs.map((row, idx) => (idx === i ? { ...row, value: e.target.value } : row)))
              }
              placeholder="Value"
            />
            <button
              onClick={() => onChange(pairs.filter((_, idx) => idx !== i))}
              className="rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
              aria-label={`Delete ${label.toLowerCase()} row`}
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
    </Field>
  );
}

function ToggleRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <div className="text-[13px] font-medium">{label}</div>
        {hint && <p className="text-xs text-sub">{hint}</p>}
      </div>
      <Toggle checked={checked} onChange={onChange} />
    </div>
  );
}

function FormShell({
  children,
  error,
  saveLabel,
  onSave,
  onCancel,
}: {
  children: React.ReactNode;
  error: string | null;
  saveLabel: string;
  onSave: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="mt-3 space-y-3 rounded-lg border border-line bg-app/50 p-3">
      {children}
      {error && <p className="text-xs text-bad">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" variant="primary" onClick={onSave}>
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}

function EndCallForm({
  initial,
  onSave,
  onCancel,
}: {
  initial: Tool;
  onSave: (tool: Tool) => void;
  onCancel: () => void;
}) {
  const [description, setDescription] = useState(initial.description ?? "");
  return (
    <FormShell
      error={null}
      saveLabel="Save"
      onCancel={onCancel}
      onSave={() =>
        onSave({ ...initial, type: "end_call", description: description.trim() })
      }
    >
      <Field label="Description" hint="When should the agent end the call?">
        <textarea
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="End the call with user."
          className={TEXTAREA}
          autoFocus
        />
      </Field>
    </FormShell>
  );
}

function TransferForm({
  initial,
  takenNames,
  onSave,
  onCancel,
}: {
  initial?: Tool;
  takenNames: string[];
  onSave: (tool: Tool) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "transfer_call");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [number, setNumber] = useState(
    initial?.transfer_destination?.number ?? initial?.number ?? "",
  );
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const trimmedNumber = number.trim();
    if (!trimmedName) return setError("Name is required");
    if (!NAME_RE.test(trimmedName)) {
      return setError("Name may only contain letters, digits, _ and - (max 64 chars)");
    }
    if (takenNames.includes(trimmedName)) {
      return setError("A function with this name already exists");
    }
    if (!isE164(trimmedNumber)) {
      return setError("Destination must be an E.164 phone number, e.g. +14155550123");
    }
    onSave({
      ...initial,
      type: "transfer_call",
      name: trimmedName,
      description: description.trim(),
      transfer_destination: {
        ...initial?.transfer_destination,
        type: "predefined",
        number: trimmedNumber,
      },
    });
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <Field label="Name">
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="transfer_call"
          maxLength={64}
          autoFocus
        />
      </Field>
      <Field label="Description">
        <textarea
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="When should the agent transfer the call?"
          className={TEXTAREA}
        />
      </Field>
      <Field label="Destination phone number">
        <TextInput
          value={number}
          onChange={(e) => setNumber(e.target.value)}
          placeholder="+14155550123"
        />
      </Field>
    </FormShell>
  );
}

function CustomForm({
  initial,
  takenNames,
  onSave,
  onCancel,
}: {
  initial?: Tool;
  takenNames: string[];
  onSave: (tool: Tool) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [method, setMethod] = useState(initial?.method ?? "POST");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [timeoutStr, setTimeoutStr] = useState(String(initial?.timeout_ms ?? 120000));
  const [headers, setHeaders] = useState<Pair[]>(toPairs(initial?.headers));
  const [queryParams, setQueryParams] = useState<Pair[]>(toPairs(initial?.query_params));
  const [parameters, setParameters] = useState(
    initial?.parameters ? JSON.stringify(initial.parameters, null, 2) : "",
  );
  const [argsAtRoot, setArgsAtRoot] = useState(initial?.args_at_root ?? true);
  const [speakDuring, setSpeakDuring] = useState(initial?.speak_during_execution ?? false);
  const [executionMessage, setExecutionMessage] = useState(
    initial?.execution_message_description ?? "",
  );
  const [speakAfter, setSpeakAfter] = useState(initial?.speak_after_execution ?? true);
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const trimmedUrl = url.trim();
    if (!trimmedName) return setError("Name is required");
    if (!NAME_RE.test(trimmedName)) {
      return setError("Name may only contain letters, digits, _ and - (max 64 chars)");
    }
    if (takenNames.includes(trimmedName)) {
      return setError("A function with this name already exists");
    }
    if (!trimmedUrl) return setError("URL is required");
    const timeoutMs = Number(timeoutStr);
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 600000) {
      return setError("Timeout must be between 1000 and 600000 ms");
    }
    let parsedParams: Record<string, unknown> | undefined;
    if (parameters.trim()) {
      let parsed: unknown;
      try {
        parsed = JSON.parse(parameters);
      } catch {
        return setError("Parameters is not valid JSON");
      }
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return setError("Parameters must be a JSON object (a JSON schema)");
      }
      parsedParams = parsed as Record<string, unknown>;
    }
    // Spread the original entry so unknown stored fields survive an edit.
    const tool: Tool = {
      ...initial,
      type: "custom",
      name: trimmedName,
      description: description.trim(),
      method,
      url: trimmedUrl,
      timeout_ms: timeoutMs,
      args_at_root: argsAtRoot,
      speak_during_execution: speakDuring,
      speak_after_execution: speakAfter,
    };
    // Empty optional groups are omitted from the wire shape entirely.
    delete tool.headers;
    delete tool.query_params;
    delete tool.parameters;
    delete tool.execution_message_description;
    const headerObj = fromPairs(headers);
    if (headerObj) tool.headers = headerObj;
    const queryObj = fromPairs(queryParams);
    if (queryObj) tool.query_params = queryObj;
    if (parsedParams) tool.parameters = parsedParams;
    if (speakDuring && executionMessage.trim()) {
      tool.execution_message_description = executionMessage.trim();
    }
    onSave(tool);
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <Field label="Name">
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. book_appointment"
          maxLength={64}
          autoFocus
        />
      </Field>
      <Field label="Description">
        <textarea
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="When should the agent call this function?"
          className={TEXTAREA}
        />
      </Field>
      <div className="flex items-start gap-2">
        <Field label="Method" className="w-28 shrink-0">
          <Select value={method} onChange={setMethod} className="w-full" options={METHOD_OPTIONS} />
        </Field>
        <Field label="URL" className="grow">
          <TextInput
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://your-server.com/tool"
          />
        </Field>
      </div>
      <Field label="Timeout (ms)" hint="1000 – 600000">
        <TextInput
          type="number"
          min={1000}
          max={600000}
          value={timeoutStr}
          onChange={(e) => setTimeoutStr(e.target.value)}
        />
      </Field>
      <PairRows label="Headers" addLabel="Add header" pairs={headers} onChange={setHeaders} />
      <PairRows
        label="Query Parameters"
        addLabel="Add query parameter"
        pairs={queryParams}
        onChange={setQueryParams}
      />
      <Field label="Parameters" hint="JSON schema object describing the function arguments.">
        <textarea
          rows={6}
          value={parameters}
          onChange={(e) => setParameters(e.target.value)}
          placeholder={'{\n  "type": "object",\n  "properties": {}\n}'}
          spellCheck={false}
          className={cn(TEXTAREA, "resize-y font-mono text-xs")}
        />
      </Field>
      <ToggleRow
        label="Payload: args only"
        hint="Send arguments flat at the root of the request body."
        checked={argsAtRoot}
        onChange={setArgsAtRoot}
      />
      <ToggleRow
        label="Speak during execution"
        hint="The agent says something while the function runs."
        checked={speakDuring}
        onChange={setSpeakDuring}
      />
      {speakDuring && (
        <Field label="Execution message" hint="Optional: what should the agent say?">
          <TextInput
            value={executionMessage}
            onChange={(e) => setExecutionMessage(e.target.value)}
            placeholder="e.g. Let me check that for you."
          />
        </Field>
      )}
      <ToggleRow
        label="Speak after execution"
        hint="The agent responds with the function result."
        checked={speakAfter}
        onChange={setSpeakAfter}
      />
    </FormShell>
  );
}

function AddMenu({
  hasEndCall,
  onPick,
}: {
  hasEndCall: boolean;
  onPick: (kind: ToolKind) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useClickOutside(
    ref,
    useCallback(() => setOpen(false), []),
  );

  const items: { kind: ToolKind; label: string; icon: LucideIcon; disabled?: boolean }[] = [
    { kind: "end_call", label: "End Call", icon: PhoneOff, disabled: hasEndCall },
    { kind: "transfer_call", label: "Call Transfer", icon: PhoneForwarded },
    { kind: "custom", label: "Custom Function", icon: Wrench },
  ];

  return (
    <div ref={ref} className="relative mt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> Add
      </button>
      {open && (
        <div className="absolute left-0 top-full z-20 mt-1 w-48 rounded-lg border border-line bg-white p-1 shadow-lg">
          {items.map((item) => (
            <button
              key={item.kind}
              disabled={item.disabled}
              title={item.disabled ? "Only one End Call function is allowed" : undefined}
              onClick={() => {
                setOpen(false);
                onPick(item.kind);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left text-[13px] font-medium hover:bg-app cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <item.icon className="size-3.5 text-sub" /> {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function FunctionsSection({
  tools,
  onChange,
}: {
  tools: Tool[];
  onChange: (tools: Tool[]) => void;
}) {
  // index === null → adding a new tool of `kind`; otherwise editing tools[index].
  const [form, setForm] = useState<{ kind: ToolKind; index: number | null } | null>(null);

  const hasEndCall = tools.some((t) => t.type === "end_call");
  const takenNames = tools.filter((_, i) => i !== form?.index).map((t) => t.name);

  const saveTool = (tool: Tool) => {
    if (form?.index != null) {
      onChange(tools.map((t, i) => (i === form.index ? tool : t)));
    } else {
      onChange([...tools, tool]);
    }
    setForm(null);
  };

  const removeTool = (i: number) => {
    onChange(tools.filter((_, idx) => idx !== i));
    if (form?.index != null) {
      if (form.index === i) setForm(null);
      else if (form.index > i) setForm({ ...form, index: form.index - 1 });
    }
  };

  const startAdd = (kind: ToolKind) => {
    if (kind === "end_call") {
      if (hasEndCall) return;
      onChange([
        ...tools,
        { type: "end_call", name: "end_call", description: "End the call with user." },
      ]);
      return;
    }
    setForm({ kind, index: null });
  };

  const kindOf = (t: Tool): ToolKind | null =>
    t.type === "end_call" || t.type === "transfer_call" || t.type === "custom"
      ? t.type
      : null;

  const editing = form?.index != null ? tools[form.index] : undefined;

  return (
    <div>
      {tools.length === 0 && !form && (
        <p className="text-[13px] text-sub">No functions attached.</p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {tools.map((f, i) => {
          const kind = kindOf(f);
          return (
            <span
              key={`${f.name}-${i}`}
              className={cn(
                "group inline-flex items-center gap-1 rounded-full border bg-white py-1 pl-3 pr-1.5 text-[12.5px] font-medium shadow-sm",
                form?.index === i ? "border-accent" : "border-line",
              )}
            >
              {kind ? (
                <button
                  onClick={() => setForm({ kind, index: i })}
                  className="font-mono hover:underline cursor-pointer"
                  title={`Edit ${f.name}`}
                >
                  {f.name}
                </button>
              ) : (
                <span className="font-mono">{f.name}</span>
              )}
              {f.type && f.type !== "custom" && (
                <span className="rounded bg-app px-1 py-0.5 font-mono text-[10px] text-sub">
                  {f.type}
                </span>
              )}
              {kind && (
                <button
                  onClick={() => setForm({ kind, index: i })}
                  className="rounded p-0.5 text-faint hover:bg-app hover:text-ink cursor-pointer"
                  aria-label={`Edit ${f.name}`}
                >
                  <Pencil className="size-3" />
                </button>
              )}
              <button
                onClick={() => removeTool(i)}
                className="rounded p-0.5 text-faint hover:bg-app hover:text-bad cursor-pointer"
                aria-label={`Delete ${f.name}`}
              >
                <Trash2 className="size-3" />
              </button>
            </span>
          );
        })}
      </div>

      {form?.kind === "end_call" && editing && (
        <EndCallForm
          key={`end-${form.index}`}
          initial={editing}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {form?.kind === "transfer_call" && (
        <TransferForm
          key={`transfer-${form.index ?? "new"}`}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {form?.kind === "custom" && (
        <CustomForm
          key={`custom-${form.index ?? "new"}`}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}

      {!form && <AddMenu hasEndCall={hasEndCall} onPick={startAdd} />}
    </div>
  );
}
