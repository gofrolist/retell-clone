"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { api, type WorkspaceType } from "@/lib/api";
import { enterWorkspace } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { Briefcase, MessageCircle, Terminal, Users } from "lucide-react";
import { useState, type ComponentType } from "react";

const PERSONAS: {
  value: WorkspaceType;
  label: string;
  hint: string;
  Icon: ComponentType<{ className?: string }>;
}[] = [
  { value: "business", label: "Business", hint: "Answer our phones", Icon: Briefcase },
  { value: "agency", label: "Agency", hint: "Building for clients", Icon: Users },
  { value: "developer", label: "Developer", hint: "Embedding Arhiteq", Icon: Terminal },
  { value: "other", label: "Other", hint: "None of the above", Icon: MessageCircle },
];

export default function CreateWorkspaceModal({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [persona, setPersona] = useState<WorkspaceType | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    setName("");
    setPersona(null);
    setError(null);
    onClose();
  };

  const create = async () => {
    setSaving(true);
    setError(null);
    try {
      const ws = await api.createWorkspace({ name: name.trim(), workspace_type: persona });
      if (!ws.token || !ws.expires_at) {
        // API-key (dev) mode: the backend can't mint a session, so there is
        // nothing to switch into. Say so instead of silently doing nothing.
        setError("Workspace created, but switching needs a Google sign-in session.");
        return;
      }
      // Leaves this page — no need to unwind the saving state.
      enterWorkspace({
        token: ws.token,
        expires_at: ws.expires_at,
        workspace_id: ws.workspace_id,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create workspace");
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Create your workspace"
      width="max-w-xl"
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancel
          </Button>
          <Button variant="primary" onClick={create} disabled={saving || !name.trim()}>
            {saving ? "Creating…" : "Save"}
          </Button>
        </>
      }
    >
      <div className="space-y-5">
        <Field label="Workspace name">
          <TextInput
            autoFocus
            placeholder="Acme Company"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && name.trim() && !saving) create();
            }}
          />
        </Field>

        <div>
          <div className="text-[13px] font-medium">What best describes you?</div>
          <p className="mt-0.5 text-[12.5px] text-sub">
            Recorded on the workspace so we can tailor it later. Optional.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-3">
            {PERSONAS.map(({ value, label, hint, Icon }) => (
              <button
                key={value}
                type="button"
                // Re-clicking the chosen card clears it: the field is optional
                // and there's no other way back to "unset".
                onClick={() => setPersona((p) => (p === value ? null : value))}
                aria-pressed={persona === value}
                className={cn(
                  "rounded-lg border p-3.5 text-left transition-colors cursor-pointer",
                  persona === value
                    ? "border-accent bg-accent/5 ring-2 ring-accent/15"
                    : "border-line hover:bg-app",
                )}
              >
                <Icon className="size-4 text-accent-deep" />
                <div className="mt-2 text-[13px] font-medium">{label}</div>
                <div className="text-[12.5px] text-sub">{hint}</div>
              </button>
            ))}
          </div>
        </div>

        {error && <p className="text-[12.5px] text-bad">{error}</p>}
      </div>
    </Modal>
  );
}
