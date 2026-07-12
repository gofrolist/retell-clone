"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Select from "@/components/ui/Select";
import Toggle from "@/components/ui/Toggle";
import type { RawLlm } from "@/lib/api";
import { cn, isE164 } from "@/lib/utils";
import { useClickOutside } from "@/lib/useClickOutside";
import {
  ArrowLeftRight,
  CalendarCheck,
  CalendarSearch,
  Hash,
  MessageSquareText,
  Pencil,
  PhoneForwarded,
  PhoneOff,
  Plus,
  Trash2,
  Variable,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useRef, useState } from "react";

type Tool = NonNullable<RawLlm["general_tools"]>[number];
type ToolVariable = NonNullable<Tool["variables"]>[number];
const TOOL_KINDS = [
  "end_call",
  "transfer_call",
  "custom",
  "press_digit",
  "check_availability_cal",
  "book_appointment_cal",
  "send_sms",
  "extract_dynamic_variable",
  "agent_swap",
] as const;
type ToolKind = (typeof TOOL_KINDS)[number];

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
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
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
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
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

/** Shared name check for all typed forms; returns an error string or null. */
function nameError(name: string, takenNames: string[]): string | null {
  if (!name) return "Name is required";
  if (!NAME_RE.test(name)) {
    return "Name may only contain letters, digits, _ and - (max 64 chars)";
  }
  if (takenNames.includes(name)) return "A function with this name already exists";
  return null;
}

function NameDescriptionFields({
  name,
  setName,
  namePlaceholder,
  description,
  setDescription,
  descriptionPlaceholder,
}: {
  name: string;
  setName: (v: string) => void;
  namePlaceholder: string;
  description: string;
  setDescription: (v: string) => void;
  descriptionPlaceholder: string;
}) {
  return (
    <>
      <Field label="Name">
        <TextInput
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={namePlaceholder}
          maxLength={64}
          autoFocus
        />
      </Field>
      <Field label="Description">
        <textarea
          rows={2}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder={descriptionPlaceholder}
          className={TEXTAREA}
        />
      </Field>
    </>
  );
}

function PressDigitForm({
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
  const [name, setName] = useState(initial?.name ?? "press_digit");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [delayStr, setDelayStr] = useState(String(initial?.delay_ms ?? 1000));
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
    const delayMs = Number(delayStr);
    if (!Number.isInteger(delayMs) || delayMs < 0 || delayMs > 5000) {
      return setError("Delay must be between 0 and 5000 ms");
    }
    onSave({
      ...initial,
      type: "press_digit",
      name: trimmedName,
      description: description.trim(),
      delay_ms: delayMs,
    });
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <NameDescriptionFields
        name={name}
        setName={setName}
        namePlaceholder="press_digit"
        description={description}
        setDescription={setDescription}
        descriptionPlaceholder="Navigate IVR menus by pressing keypad digits."
      />
      <Field
        label="Delay (ms)"
        hint="Pause before pressing, so slow IVR menus finish speaking. 0 – 5000."
      >
        <TextInput
          type="number"
          min={0}
          max={5000}
          value={delayStr}
          onChange={(e) => setDelayStr(e.target.value)}
        />
      </Field>
    </FormShell>
  );
}

function CalendarForm({
  kind,
  initial,
  takenNames,
  onSave,
  onCancel,
}: {
  kind: "check_availability_cal" | "book_appointment_cal";
  initial?: Tool;
  takenNames: string[];
  onSave: (tool: Tool) => void;
  onCancel: () => void;
}) {
  const defaultName =
    kind === "check_availability_cal" ? "check_availability" : "book_appointment";
  const [name, setName] = useState(initial?.name ?? defaultName);
  const [description, setDescription] = useState(initial?.description ?? "");
  const [apiKey, setApiKey] = useState(initial?.cal_api_key ?? "");
  const [eventTypeId, setEventTypeId] = useState(String(initial?.event_type_id ?? ""));
  const [timezone, setTimezone] = useState(initial?.timezone ?? "");
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
    if (!apiKey.trim()) return setError("Cal.com API key is required");
    const trimmedEventType = eventTypeId.trim();
    if (!trimmedEventType) return setError("Event type ID is required");
    const tool: Tool = {
      ...initial,
      type: kind,
      name: trimmedName,
      description: description.trim(),
      cal_api_key: apiKey.trim(),
      // Keep numeric IDs numeric on the wire; {{var}} references stay strings.
      event_type_id: /^\d+$/.test(trimmedEventType)
        ? Number(trimmedEventType)
        : trimmedEventType,
    };
    delete tool.timezone;
    if (timezone.trim()) tool.timezone = timezone.trim();
    onSave(tool);
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <NameDescriptionFields
        name={name}
        setName={setName}
        namePlaceholder={defaultName}
        description={description}
        setDescription={setDescription}
        descriptionPlaceholder={
          kind === "check_availability_cal"
            ? "When should the agent check the calendar?"
            : "When should the agent book the appointment?"
        }
      />
      <Field label="Cal.com API key">
        <TextInput
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder="cal_live_..."
        />
      </Field>
      <Field label="Event type ID" hint="Numeric Cal.com event type ID, or a {{variable}}.">
        <TextInput
          value={eventTypeId}
          onChange={(e) => setEventTypeId(e.target.value)}
          placeholder="e.g. 12345"
        />
      </Field>
      <Field label="Timezone" hint="IANA timezone, e.g. America/Los_Angeles. Optional.">
        <TextInput
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          placeholder="UTC"
        />
      </Field>
    </FormShell>
  );
}

function SendSmsForm({
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
  const initialContent = initial?.sms_content;
  // Absent type means predefined on the Retell wire; template is a distinct
  // variant we must not silently rewrite.
  const initialMode =
    initialContent?.type === "template"
      ? "template"
      : initialContent?.type === "predefined" || (!initialContent?.type && initialContent?.content)
        ? "predefined"
        : "inferred";
  const [name, setName] = useState(initial?.name ?? "send_sms");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [mode, setMode] = useState(initialMode);
  const [content, setContent] = useState(
    initialContent?.content ?? initialContent?.prompt ?? "",
  );
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
    if (mode === "predefined" && !content.trim()) {
      return setError("Message content is required for a fixed message");
    }
    // Spread the original sms_content so unknown Retell fields survive an
    // edit; the template variant is kept verbatim.
    const smsContent =
      mode === "template"
        ? { ...initialContent }
        : mode === "predefined"
          ? { ...initialContent, type: "predefined", content: content.trim() }
          : { ...initialContent, type: "inferred", prompt: content.trim() };
    onSave({
      ...initial,
      type: "send_sms",
      name: trimmedName,
      description: description.trim(),
      sms_content: smsContent,
    });
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <NameDescriptionFields
        name={name}
        setName={setName}
        namePlaceholder="send_sms"
        description={description}
        setDescription={setDescription}
        descriptionPlaceholder="When should the agent text the caller?"
      />
      <Field label="Message content">
        <Select
          value={mode}
          onChange={setMode}
          className="w-full"
          options={[
            { value: "inferred", label: "Generated from prompt" },
            { value: "predefined", label: "Fixed message" },
            ...(initialMode === "template"
              ? [{ value: "template", label: `Template (${initialContent?.template ?? "…"})` }]
              : []),
          ]}
        />
      </Field>
      {mode !== "template" && (
        <Field
          label={mode === "predefined" ? "Message" : "Prompt"}
          hint={
            mode === "predefined"
              ? "Sent verbatim; {{variables}} are resolved."
              : "The agent writes the SMS from this prompt and the conversation."
          }
        >
          <textarea
            rows={3}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            placeholder={
              mode === "predefined"
                ? "Hi {{name}}, here is our address: …"
                : "Text the caller a summary of the appointment we booked."
            }
            className={TEXTAREA}
          />
        </Field>
      )}
    </FormShell>
  );
}

const VARIABLE_TYPE_OPTIONS = ["string", "enum", "boolean", "number"].map((t) => ({
  value: t,
  label: t,
}));

function ExtractVariablesForm({
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
  const [name, setName] = useState(initial?.name ?? "extract_user_info");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [variables, setVariables] = useState<ToolVariable[]>(
    (initial?.variables ?? []).map((v) => ({ ...v })),
  );
  const [error, setError] = useState<string | null>(null);

  const patchVar = (i: number, patch: Partial<ToolVariable>) =>
    setVariables(variables.map((v, idx) => (idx === i ? { ...v, ...patch } : v)));

  const save = () => {
    const trimmedName = name.trim();
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
    const cleaned: ToolVariable[] = [];
    for (const v of variables) {
      const varName = (v.name ?? "").trim();
      if (!varName) continue;
      if (!NAME_RE.test(varName)) {
        return setError(`Variable name "${varName}" may only contain letters, digits, _ and -`);
      }
      const type = v.type ?? "string";
      // Spread the original spec so unknown Retell fields (examples,
      // conditional_prompt, …) survive an edit.
      const spec: ToolVariable = {
        ...v,
        name: varName,
        type,
        description: (v.description ?? "").trim(),
      };
      if (type === "enum") {
        const choices = (v.choices ?? []).map((c) => c.trim()).filter(Boolean);
        if (choices.length === 0) {
          return setError(`Variable "${varName}" needs at least one choice`);
        }
        spec.choices = choices;
      } else {
        delete spec.choices;
      }
      if (v.required) spec.required = true;
      else delete spec.required;
      cleaned.push(spec);
    }
    if (cleaned.length === 0) return setError("Add at least one variable");
    onSave({
      ...initial,
      type: "extract_dynamic_variable",
      name: trimmedName,
      description: description.trim(),
      variables: cleaned,
    });
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <NameDescriptionFields
        name={name}
        setName={setName}
        namePlaceholder="extract_user_info"
        description={description}
        setDescription={setDescription}
        descriptionPlaceholder="Extract these details as soon as the caller mentions them."
      />
      <Field label="Variables">
        <div className="space-y-2">
          {variables.map((v, i) => (
            <div key={i} className="space-y-1.5 rounded-lg border border-line bg-white p-2">
              <div className="flex items-center gap-1.5">
                <TextInput
                  value={v.name ?? ""}
                  onChange={(e) => patchVar(i, { name: e.target.value })}
                  placeholder="variable_name"
                />
                <Select
                  value={v.type ?? "string"}
                  onChange={(type) => patchVar(i, { type })}
                  className="w-28 shrink-0"
                  options={VARIABLE_TYPE_OPTIONS}
                />
                <button
                  onClick={() => setVariables(variables.filter((_, idx) => idx !== i))}
                  className="rounded p-1 text-faint hover:bg-app hover:text-bad cursor-pointer"
                  aria-label="Delete variable"
                >
                  <Trash2 className="size-3.5" />
                </button>
              </div>
              <TextInput
                value={v.description ?? ""}
                onChange={(e) => patchVar(i, { description: e.target.value })}
                placeholder="What is this variable?"
              />
              {(v.type ?? "string") === "enum" && (
                <TextInput
                  value={(v.choices ?? []).join(", ")}
                  onChange={(e) => patchVar(i, { choices: e.target.value.split(",") })}
                  placeholder="Choices, comma separated"
                />
              )}
              <ToggleRow
                label="Required"
                checked={Boolean(v.required)}
                onChange={(required) => patchVar(i, { required })}
              />
            </div>
          ))}
          <button
            onClick={() => setVariables([...variables, { name: "", type: "string" }])}
            className="inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
          >
            <Plus className="size-3.5" /> Add variable
          </button>
        </div>
      </Field>
    </FormShell>
  );
}

function AgentSwapForm({
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
  const [name, setName] = useState(initial?.name ?? "agent_swap");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [agentId, setAgentId] = useState(initial?.agent_id ?? "");
  const [analysisSetting, setAnalysisSetting] = useState(
    initial?.post_call_analysis_setting ?? "both_agents",
  );
  const [error, setError] = useState<string | null>(null);

  const save = () => {
    const trimmedName = name.trim();
    const nameErr = nameError(trimmedName, takenNames);
    if (nameErr) return setError(nameErr);
    if (!agentId.trim()) return setError("Destination agent ID is required");
    onSave({
      ...initial,
      type: "agent_swap",
      name: trimmedName,
      description: description.trim(),
      agent_id: agentId.trim(),
      post_call_analysis_setting: analysisSetting,
    });
  };

  return (
    <FormShell
      error={error}
      saveLabel={initial ? "Save" : "Add function"}
      onSave={save}
      onCancel={onCancel}
    >
      <NameDescriptionFields
        name={name}
        setName={setName}
        namePlaceholder="agent_swap"
        description={description}
        setDescription={setDescription}
        descriptionPlaceholder="When should the call be handed to the other agent?"
      />
      <Field label="Destination agent ID">
        <TextInput
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
          placeholder="agent_..."
        />
      </Field>
      <Field label="Post-call analysis">
        <Select
          value={analysisSetting}
          onChange={setAnalysisSetting}
          className="w-full"
          options={[
            { value: "both_agents", label: "Both agents" },
            { value: "only_destination_agent", label: "Only destination agent" },
          ]}
        />
      </Field>
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
    { kind: "custom", label: "Custom Function", icon: Wrench },
    { kind: "end_call", label: "End Call", icon: PhoneOff, disabled: hasEndCall },
    { kind: "transfer_call", label: "Call Transfer", icon: PhoneForwarded },
    { kind: "agent_swap", label: "Agent Swap", icon: ArrowLeftRight },
    { kind: "press_digit", label: "Press Digit (IVR Navigation)", icon: Hash },
    {
      kind: "check_availability_cal",
      label: "Check Calendar Availability (Cal.com)",
      icon: CalendarSearch,
    },
    { kind: "book_appointment_cal", label: "Book on the Calendar (Cal.com)", icon: CalendarCheck },
    { kind: "send_sms", label: "Send SMS", icon: MessageSquareText },
    { kind: "extract_dynamic_variable", label: "Extract Dynamic Variables", icon: Variable },
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
        <div className="absolute left-0 top-full z-20 mt-1 w-72 rounded-lg border border-line bg-white p-1 shadow-lg">
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
    TOOL_KINDS.includes(t.type as ToolKind) ? (t.type as ToolKind) : null;

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
      {form?.kind === "press_digit" && (
        <PressDigitForm
          key={`press-${form.index ?? "new"}`}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {(form?.kind === "check_availability_cal" || form?.kind === "book_appointment_cal") && (
        <CalendarForm
          key={`cal-${form.kind}-${form.index ?? "new"}`}
          kind={form.kind}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {form?.kind === "send_sms" && (
        <SendSmsForm
          key={`sms-${form.index ?? "new"}`}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {form?.kind === "extract_dynamic_variable" && (
        <ExtractVariablesForm
          key={`extract-${form.index ?? "new"}`}
          initial={editing}
          takenNames={takenNames}
          onSave={saveTool}
          onCancel={() => setForm(null)}
        />
      )}
      {form?.kind === "agent_swap" && (
        <AgentSwapForm
          key={`swap-${form.index ?? "new"}`}
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
