"use client";

import { PairRows, toPairs, fromPairs, type Pair } from "@/components/editor/PairRows";
import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import type { McpServer } from "@/lib/api";
import { Pencil, Plus, Server, Trash2 } from "lucide-react";
import { useState } from "react";

export default function McpSection({
  mcps,
  onChange,
}: {
  mcps: McpServer[];
  onChange: (v: McpServer[] | null) => void;
}) {
  // null = closed; -1 = adding; >= 0 = editing that index.
  const [editing, setEditing] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [timeoutMs, setTimeoutMs] = useState("");
  const [headers, setHeaders] = useState<Pair[]>([]);
  const [queryParams, setQueryParams] = useState<Pair[]>([]);
  const [formError, setFormError] = useState<string | null>(null);

  const openEditor = (index: number) => {
    const server = index >= 0 ? mcps[index] : undefined;
    setName(server?.name ?? "");
    setUrl(server?.url ?? "");
    setTimeoutMs(server?.timeout_ms ? String(server.timeout_ms) : "");
    setHeaders(toPairs(server?.headers));
    setQueryParams(toPairs(server?.query_params));
    setFormError(null);
    setEditing(index);
  };

  const save = () => {
    if (!name.trim()) {
      setFormError("Name is required");
      return;
    }
    if (!/^https:\/\/.+/i.test(url.trim())) {
      setFormError("URL must start with https://");
      return;
    }
    const timeout = timeoutMs.trim() ? Number(timeoutMs) : undefined;
    if (timeout !== undefined && (!Number.isInteger(timeout) || timeout <= 0)) {
      setFormError("Timeout must be a positive whole number of milliseconds");
      return;
    }
    const parsedHeaders = fromPairs(headers);
    const parsedQueryParams = fromPairs(queryParams);
    const server: McpServer = {
      name: name.trim(),
      url: url.trim(),
      ...(parsedHeaders ? { headers: parsedHeaders } : {}),
      ...(parsedQueryParams ? { query_params: parsedQueryParams } : {}),
      ...(timeout !== undefined ? { timeout_ms: timeout } : {}),
    };
    const next =
      editing !== null && editing >= 0
        ? mcps.map((s, i) => (i === editing ? server : s))
        : [...mcps, server];
    onChange(next);
    setEditing(null);
  };

  const remove = (index: number) => {
    const next = mcps.filter((_, i) => i !== index);
    onChange(next.length ? next : null);
  };

  return (
    <div>
      <p className="text-[13px] text-sub">
        Connect MCP servers to give your agent access to external tools. Servers are saved with
        the agent&apos;s LLM; tool execution by the voice worker is rolling out.
      </p>

      {mcps.length > 0 && (
        <div className="mt-3 divide-y divide-line rounded-lg border border-line">
          {mcps.map((s, i) => (
            <div key={`${s.name}-${i}`} className="flex items-center gap-2.5 px-3 py-2">
              <Server className="size-4 shrink-0 text-sub" />
              <div className="min-w-0 grow">
                <div className="truncate text-[13px] font-medium">{s.name}</div>
                <div className="truncate text-xs text-sub">{s.url}</div>
              </div>
              <button
                onClick={() => openEditor(i)}
                className="rounded p-1 text-sub hover:bg-app hover:text-ink cursor-pointer"
                aria-label={`Edit ${s.name}`}
              >
                <Pencil className="size-3.5" />
              </button>
              <button
                onClick={() => remove(i)}
                className="rounded p-1 text-sub hover:bg-app hover:text-bad cursor-pointer"
                aria-label={`Delete ${s.name}`}
              >
                <Trash2 className="size-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      <button
        onClick={() => openEditor(-1)}
        className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
      >
        <Plus className="size-3.5" /> Add MCP Server
      </button>

      <Modal
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing !== null && editing >= 0 ? "Edit MCP Server" : "Add MCP Server"}
        width="max-w-lg"
        footer={
          <>
            <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={save}>
              Save
            </Button>
          </>
        }
      >
        <div className="space-y-4">
          <Field label="Name">
            <TextInput
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. calendar-tools"
            />
          </Field>
          <Field label="Server URL">
            <TextInput
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://mcp.example.com/sse"
            />
          </Field>
          <Field label="Timeout (ms)" hint="Optional; how long a tool call may run.">
            <TextInput
              value={timeoutMs}
              onChange={(e) => setTimeoutMs(e.target.value)}
              inputMode="numeric"
              placeholder="e.g. 10000"
            />
          </Field>
          <PairRows
            label="Headers"
            addLabel="Add header"
            pairs={headers}
            onChange={setHeaders}
            keyPlaceholder="Header"
            valuePlaceholder="Value"
          />
          <PairRows
            label="Query Parameters"
            addLabel="Add query parameter"
            pairs={queryParams}
            onChange={setQueryParams}
            keyPlaceholder="Key"
            valuePlaceholder="Value"
          />
          {formError && <p className="text-[12.5px] text-bad">{formError}</p>}
        </div>
      </Modal>
    </div>
  );
}
