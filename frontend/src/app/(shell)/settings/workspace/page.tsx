"use client";

import SettingsCard, { SettingsPageHeader } from "@/components/settings/SettingsCard";
import Badge from "@/components/ui/Badge";
import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import { api } from "@/lib/api";
import {
  getServerSessionSnapshot,
  getSessionSnapshot,
  subscribeSession,
} from "@/lib/auth";
import { useEffect, useState, useSyncExternalStore } from "react";

export default function WorkspacePage() {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState<string | null>(null);

  // The single member is whoever is signed in (or the dev API-key identity).
  const session = useSyncExternalStore(
    subscribeSession,
    getSessionSnapshot,
    getServerSessionSnapshot,
  );

  useEffect(() => {
    api
      .getWorkspace()
      .then((ws) => setName(ws.name))
      .catch(() => {}); // backend banner covers unreachable
  }, []);

  const save = async () => {
    setSaving(true);
    setSaveState("idle");
    setSaveError(null);
    try {
      const ws = await api.updateWorkspace({ name: name.trim() });
      setName(ws.name);
      setSaveState("saved");
      setTimeout(() => setSaveState("idle"), 2000);
    } catch (e) {
      setSaveState("error");
      setSaveError(e instanceof Error ? e.message : "Failed to rename workspace");
    } finally {
      setSaving(false);
    }
  };

  const memberLabel = session?.email ?? "API key (dev)";
  const memberInitial = (session?.name ?? session?.email ?? "K").charAt(0).toUpperCase();

  return (
    <div className="h-full overflow-y-auto px-8 py-6">
      <div className="mx-auto max-w-2xl">
        <SettingsPageHeader title="Workspace" />
        <div className="space-y-4">
          <SettingsCard title="Workspace Name">
            <div className="flex items-center gap-2">
              <TextInput value={name} onChange={(e) => setName(e.target.value)} />
              <Button variant="primary" onClick={save} disabled={saving || !name.trim()}>
                {saving ? "Saving…" : saveState === "saved" ? "Saved" : "Save"}
              </Button>
            </div>
            {saveState === "error" && saveError && (
              <p className="mt-2 text-[12.5px] text-bad">{saveError}</p>
            )}
          </SettingsCard>

          <SettingsCard
            title="Members"
            description="People with access to this workspace."
            right={
              <Button size="sm" disabled title="Not available yet">
                Invite
              </Button>
            }
          >
            <div className="divide-y divide-line rounded-lg border border-line">
              <div className="flex items-center justify-between px-3 py-2.5">
                <span className="flex items-center gap-2.5 text-[13px]">
                  {session?.picture ? (
                    // eslint-disable-next-line @next/next/no-img-element -- tiny remote avatar; next/image needs remotePatterns config
                    <img
                      src={session.picture}
                      alt=""
                      referrerPolicy="no-referrer"
                      className="size-6 shrink-0 rounded-full object-cover"
                    />
                  ) : (
                    <span className="flex size-6 items-center justify-center rounded-full bg-gradient-to-br from-amber-400 to-rose-400 text-[10px] font-semibold text-white">
                      {memberInitial}
                    </span>
                  )}
                  <span>
                    {memberLabel}
                    {session?.name && (
                      <span className="ml-1.5 text-sub">{session.name}</span>
                    )}
                  </span>
                </span>
                <Badge tone="blue">Owner</Badge>
              </div>
            </div>
          </SettingsCard>

          <SettingsCard
            title="Billing"
            description="Current plan and payment method."
            right={<Badge tone="blue">Pay As You Go</Badge>}
          >
            <Field label="Billing Email">
              <div className="flex items-center gap-2">
                <TextInput value={session?.email ?? ""} readOnly disabled />
                <Button disabled title="Not available yet">
                  Update
                </Button>
              </div>
            </Field>
          </SettingsCard>

          <SettingsCard
            title="Danger Zone"
            description="Deleting a workspace removes all agents, calls and data permanently."
            right={
              <Button size="sm" variant="danger" disabled title="Not available yet">
                Delete Workspace
              </Button>
            }
          />
        </div>
      </div>
    </div>
  );
}
