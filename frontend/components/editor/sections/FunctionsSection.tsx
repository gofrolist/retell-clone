"use client";

import Button from "@/components/ui/Button";
import { TextInput } from "@/components/ui/Field";
import type { RawLlm } from "@/lib/api";
import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";

type Tool = NonNullable<RawLlm["general_tools"]>[number];

export default function FunctionsSection({
  tools,
  onChange,
}: {
  tools: Tool[];
  onChange: (tools: Tool[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [url, setUrl] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const resetForm = () => {
    setAdding(false);
    setName("");
    setDescription("");
    setUrl("");
    setFormError(null);
  };

  const addFunction = () => {
    const trimmedName = name.trim();
    const trimmedUrl = url.trim();
    if (!trimmedName) return setFormError("Name is required");
    if (!trimmedUrl) return setFormError("URL is required");
    if (tools.some((t) => t.name === trimmedName)) {
      return setFormError("A function with this name already exists");
    }
    // RawLlm.general_tools is typed with only name/type; custom functions
    // carry description/url on the wire, which the backend stores verbatim.
    const newTool = {
      type: "custom",
      name: trimmedName,
      description: description.trim(),
      url: trimmedUrl,
    } as Tool;
    onChange([...tools, newTool]);
    resetForm();
  };

  return (
    <div>
      {tools.length === 0 && !adding && (
        <p className="text-[13px] text-sub">No functions attached.</p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {tools.map((f, i) => (
          <span
            key={`${f.name}-${i}`}
            className="group inline-flex items-center gap-1.5 rounded-full border border-line bg-white py-1 pl-3 pr-1.5 text-[12.5px] font-medium shadow-sm"
          >
            <span className="font-mono">{f.name}</span>
            <button
              onClick={() => onChange(tools.filter((_, idx) => idx !== i))}
              className="rounded p-0.5 text-faint hover:bg-app hover:text-bad cursor-pointer"
              aria-label={`Delete ${f.name}`}
            >
              <Trash2 className="size-3" />
            </button>
          </span>
        ))}
      </div>
      {adding ? (
        <div className="mt-3 space-y-2 rounded-lg border border-line bg-app/50 p-3">
          <TextInput
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Function name (e.g. book_appointment)"
            autoFocus
          />
          <TextInput
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description — when should the agent call this?"
          />
          <TextInput
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://your-server.com/tool"
          />
          {formError && <p className="text-xs text-bad">{formError}</p>}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={resetForm}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" onClick={addFunction}>
              Add function
            </Button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="mt-3 inline-flex items-center gap-1 text-[13px] font-medium text-accent-deep hover:underline cursor-pointer"
        >
          <Plus className="size-3.5" /> Add
        </button>
      )}
    </div>
  );
}
